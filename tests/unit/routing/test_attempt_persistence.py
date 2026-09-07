from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.attempt_counters import (
    DAILY_SCOPE,
    QUOTA_REQUESTS_SCOPE,
    bump_attempt_counter,
)
from janus.storage.database import init_db
from janus.storage.quotas import window_id


def _registry(*configs: ProviderConfig) -> ProviderRegistry:
    registry = ProviderRegistry()
    for config in configs:
        registry.register(config)
    return registry


def _config(account_id: str, **kwargs: object) -> ProviderConfig:
    return ProviderConfig(
        id=account_id,
        prefix="cp",
        api_type="openai_compat",
        base_url="https://cp.com",
        api_key="k",
        models=["m1"],
        **kwargs,  # type: ignore[arg-type]
    )


def _target(handler: FallbackHandler, model: str = "cp/m1"):
    return handler.resolve_attempts(model)[0]


async def _reload_like_dashboard(
    old: FallbackHandler, registry: ProviderRegistry, db
) -> FallbackHandler:
    """Mirror dashboard/reload.py: fresh handler, adopt runtime state, then load."""
    handler = FallbackHandler(registry, db_path=db)
    handler.adopt_runtime_state(old)
    await handler.load_request_counts()
    await handler.load_quota_usage()
    return handler


async def test_failed_attempts_survive_provider_reload(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(
        _config("cp-1", rate_limit_rpd=2, quota_window="daily", quota_limit=2),
    )
    old = FallbackHandler(registry, db_path=db)
    target = _target(old)
    # Two failed attempts: counted in memory and the ledger, never in `usage`.
    old.record_attempt(target)
    old.record_attempt(target)
    await old._drain_persist_tasks()

    reloaded = await _reload_like_dashboard(old, registry, db)
    assert reloaded._daily_counts.get("cp-1") == 2
    assert reloaded.quota_used("cp-1", "daily") == 2
    assert not reloaded.has_rate_headroom(_target(reloaded))
    assert not reloaded.has_quota_headroom(_target(reloaded))


async def test_attempts_survive_process_restart(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(
        _config("cp-1", rate_limit_rpd=2, quota_window="daily", quota_limit=2),
    )
    first = FallbackHandler(registry, db_path=db)
    first.record_attempt(_target(first))
    first.record_attempt(_target(first))
    await first._drain_persist_tasks()

    restarted = FallbackHandler(registry, db_path=db)
    await restarted.load_request_counts()
    await restarted.load_quota_usage()
    assert restarted._daily_counts.get("cp-1") == 2
    assert restarted.quota_used("cp-1", "daily") == 2
    assert not restarted.has_rate_headroom(_target(restarted))
    assert not restarted.has_quota_headroom(_target(restarted))


async def test_reload_merge_keeps_unpersisted_memory_counts(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(_config("cp-1", rate_limit_rpd=5))
    old = FallbackHandler(registry, db_path=db)
    old.record_request("cp-1")
    old.record_request("cp-1")
    await old._drain_persist_tasks()
    # One more attempt whose ledger write has not committed yet.
    old.record_request("cp-1")

    reloaded = await _reload_like_dashboard(old, registry, db)
    assert reloaded._daily_counts.get("cp-1") == 3


async def test_daily_window_expiry_resets_ledger_seed(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(_config("cp-1", rate_limit_rpd=1))
    await bump_attempt_counter(db, DAILY_SCOPE, "cp-1", "2001-01-01", 7)
    handler = FallbackHandler(registry, db_path=db)
    await handler.load_request_counts()
    assert handler._daily_counts.get("cp-1") is None
    assert handler.has_rate_headroom(_target(handler))


async def test_quota_window_expiry_resets_ledger_seed(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(_config("cp-1", quota_window="daily", quota_limit=1))
    await bump_attempt_counter(db, QUOTA_REQUESTS_SCOPE, "cp-1", "2001-01-01", 7)
    handler = FallbackHandler(registry, db_path=db)
    await handler.load_quota_usage()
    assert handler.quota_used("cp-1", "daily") == 0
    assert handler.has_quota_headroom(_target(handler))


async def test_multi_account_and_row_quota_scopes_stay_distinct(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(
        _config("cp::uk_a", rate_limit_rpd=1, quota_window="daily", quota_limit=2),
        _config("cp::uk_b", rate_limit_rpd=1, quota_window="daily", quota_limit=2),
    )
    first = FallbackHandler(registry, db_path=db)
    targets = first.resolve_attempts("cp/m1")
    first.record_attempt(targets[0])
    await first._drain_persist_tasks()

    restarted = FallbackHandler(registry, db_path=db)
    await restarted.load_request_counts()
    await restarted.load_quota_usage()
    # RPD is per account: only the attempted account is exhausted.
    assert restarted._daily_counts.get("cp::uk_a") == 1
    assert restarted._daily_counts.get("cp::uk_b") is None
    by_account = {t.account_id: t for t in restarted.resolve_attempts("cp/m1")}
    assert not restarted.has_rate_headroom(by_account["cp::uk_a"])
    assert restarted.has_rate_headroom(by_account["cp::uk_b"])
    # Quota is per provider row: shared across expanded accounts.
    assert restarted.quota_used("cp", "daily") == 1


async def test_quota_tokens_persist_and_seed(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    registry = _registry(
        _config("cp-1", quota_window="5h", quota_limit=100, quota_metric="tokens"),
    )
    first = FallbackHandler(registry, db_path=db)
    target = _target(first)
    first.record_attempt(target)
    first.record_quota_tokens(target, 70)
    await first._drain_persist_tasks()

    restarted = FallbackHandler(registry, db_path=db)
    await restarted.load_quota_usage()
    assert restarted.quota_used("cp-1", "5h") == 70
    assert restarted.has_quota_headroom(_target(restarted))
    assert window_id("5h")  # sanity: seeded window key is the live 5h bucket

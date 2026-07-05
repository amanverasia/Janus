from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.database import init_db
from janus.storage.usage import record_usage


def _registry(*configs: ProviderConfig) -> ProviderRegistry:
    registry = ProviderRegistry()
    for config in configs:
        registry.register(config)
    return registry


def _config(account_id: str, prefix: str = "cp", **kwargs: object) -> ProviderConfig:
    return ProviderConfig(
        id=account_id,
        prefix=prefix,
        api_type="openai_compat",
        base_url="https://cp.com",
        api_key="k",
        models=["m1"],
        **kwargs,  # type: ignore[arg-type]
    )


def _target(handler: FallbackHandler, model: str = "cp/m1"):
    return handler.resolve_attempts(model)[0]


def test_no_quota_config_means_headroom():
    handler = FallbackHandler(_registry(_config("cp-1")))
    for _ in range(100):
        handler.record_attempt(_target(handler))
    assert handler.has_quota_headroom(_target(handler))


def test_request_quota_exhaustion_deprioritizes():
    registry = _registry(
        _config("cp-1", quota_window="daily", quota_limit=2),
        _config("cp-2"),
    )
    handler = FallbackHandler(registry)
    cp1 = next(t for t in handler.resolve_attempts("cp/m1") if t.account_id == "cp-1")
    handler.record_attempt(cp1)
    handler.record_attempt(cp1)
    attempts = handler.resolve_attempts("cp/m1")
    assert [t.account_id for t in attempts] == ["cp-2", "cp-1"]
    assert not handler.has_quota_headroom(cp1)


def test_token_quota_counts_tokens_not_requests():
    registry = _registry(_config("cp-1", quota_window="5h", quota_limit=100, quota_metric="tokens"))
    handler = FallbackHandler(registry)
    target = _target(handler)
    handler.record_attempt(target)
    assert handler.has_quota_headroom(target)
    handler.record_quota_tokens(target, 60)
    assert handler.has_quota_headroom(target)
    handler.record_quota_tokens(target, 60)
    assert not handler.has_quota_headroom(target)


def test_quota_shared_across_expanded_accounts():
    registry = _registry(
        _config("cp::uk_a", quota_window="daily", quota_limit=2),
        _config("cp::uk_b", quota_window="daily", quota_limit=2),
    )
    handler = FallbackHandler(registry)
    targets = handler.resolve_attempts("cp/m1")
    handler.record_attempt(targets[0])
    handler.record_attempt(targets[1])
    assert not handler.has_quota_headroom(targets[0])
    assert not handler.has_quota_headroom(targets[1])
    assert handler.quota_used("cp", "daily") == 2


def test_window_rollover_resets_counter(monkeypatch):
    registry = _registry(_config("cp-1", quota_window="daily", quota_limit=1))
    handler = FallbackHandler(registry)
    target = _target(handler)
    handler.record_attempt(target)
    assert not handler.has_quota_headroom(target)
    monkeypatch.setattr("janus.routing.fallback.window_id", lambda window, now=None: "next-window")
    assert handler.has_quota_headroom(target)
    assert handler.quota_used("cp-1", "daily") == 0


async def test_load_quota_usage_seeds_from_db(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await record_usage(db, provider_id="cp-1", input_tokens=10, output_tokens=5, status=200)
    await record_usage(db, provider_id="cp-1::uk_x", input_tokens=1, output_tokens=1, status=200)
    registry = _registry(_config("cp-1", quota_window="daily", quota_limit=2))
    handler = FallbackHandler(registry, db_path=db)
    await handler.load_quota_usage()
    assert handler.quota_used("cp-1", "daily") == 2
    assert not handler.has_quota_headroom(_target(handler))


async def test_load_quota_usage_seeds_tokens_metric(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await record_usage(db, provider_id="cp-1", input_tokens=70, output_tokens=40, status=200)
    registry = _registry(
        _config("cp-1", quota_window="monthly", quota_limit=100, quota_metric="tokens")
    )
    handler = FallbackHandler(registry, db_path=db)
    await handler.load_quota_usage()
    assert handler.quota_used("cp-1", "monthly") == 110
    assert not handler.has_quota_headroom(_target(handler))

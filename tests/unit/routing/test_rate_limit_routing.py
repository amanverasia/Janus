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


def _config(account_id: str, **kwargs: object) -> ProviderConfig:
    return ProviderConfig(
        id=account_id,
        prefix="ds",
        api_type="openai_compat",
        base_url="https://ds.com",
        api_key="k",
        models=["m1"],
        **kwargs,  # type: ignore[arg-type]
    )


def test_no_limits_means_headroom():
    registry = _registry(_config("ds-1"))
    handler = FallbackHandler(registry)
    for _ in range(50):
        handler.record_request("ds-1")
    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["ds-1"]


def test_rpm_exhausted_account_is_deprioritized():
    registry = _registry(
        _config("ds-1", rate_limit_rpm=2),
        _config("ds-2"),
    )
    handler = FallbackHandler(registry)
    handler.record_request("ds-1")
    handler.record_request("ds-1")
    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["ds-2", "ds-1"]


def test_account_with_headroom_keeps_rotation_position():
    registry = _registry(
        _config("ds-1", rate_limit_rpm=10),
        _config("ds-2"),
    )
    handler = FallbackHandler(registry)
    handler.record_request("ds-1")
    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["ds-1", "ds-2"]


def test_rpd_exhausted_account_is_deprioritized():
    registry = _registry(
        _config("ds-1", rate_limit_rpd=1),
        _config("ds-2"),
    )
    handler = FallbackHandler(registry)
    handler.record_request("ds-1")
    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["ds-2", "ds-1"]


def test_all_accounts_limited_still_returns_attempts():
    registry = _registry(
        _config("ds-1", rate_limit_rpm=1),
        _config("ds-2", rate_limit_rpm=1),
    )
    handler = FallbackHandler(registry)
    handler.record_request("ds-1")
    handler.record_request("ds-2")
    attempts = handler.resolve_attempts("ds/m1")
    assert {t.account_id for t in attempts} == {"ds-1", "ds-2"}


def test_rpm_window_expires(monkeypatch):
    registry = _registry(
        _config("ds-1", rate_limit_rpm=1),
        _config("ds-2"),
    )
    handler = FallbackHandler(registry)
    handler.record_request("ds-1")

    import janus.routing.fallback as fallback_module

    real_time = fallback_module.time.time()
    monkeypatch.setattr(fallback_module.time, "time", lambda: real_time + 61.0)
    attempts = handler.resolve_attempts("ds/m1")
    assert attempts[0].account_id == "ds-1"


def test_daily_counts_reset_on_new_day():
    registry = _registry(
        _config("ds-1", rate_limit_rpd=1),
        _config("ds-2"),
    )
    handler = FallbackHandler(registry)
    handler.record_request("ds-1")
    limited = next(t for t in handler.resolve_attempts("ds/m1") if t.account_id == "ds-1")
    assert not handler.has_rate_headroom(limited)
    handler._daily_date = "1970-01-01"
    assert handler.has_rate_headroom(limited)


async def test_load_request_counts_seeds_daily_counts(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await record_usage(db_path, provider_id="ds", model="m1", account_id="uk-1", status=200)
    await record_usage(db_path, provider_id="ds", model="m1", account_id="uk-1", status=200)

    registry = _registry(
        _config("uk-1", rate_limit_rpd=2),
        _config("uk-2"),
    )
    handler = FallbackHandler(registry, db_path=db_path)
    await handler.load_request_counts()
    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["uk-2", "uk-1"]

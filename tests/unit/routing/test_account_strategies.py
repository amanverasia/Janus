from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import AccountStrategy, FallbackHandler


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


def _three_account_registry() -> ProviderRegistry:
    return _registry(_config("ds-1"), _config("ds-2"), _config("ds-3"))


def test_fill_first_always_returns_same_first_account():
    registry = _three_account_registry()
    handler = FallbackHandler(registry)
    for _ in range(4):
        attempts = handler.resolve_attempts("ds/m1", strategy=AccountStrategy.FILL_FIRST)
        assert attempts[0].account_id == "ds-1"


def test_round_robin_advances_first_account_each_call():
    registry = _three_account_registry()
    handler = FallbackHandler(registry)
    firsts = [
        handler.resolve_attempts("ds/m1", strategy=AccountStrategy.ROUND_ROBIN)[0].account_id
        for _ in range(3)
    ]
    assert firsts == ["ds-1", "ds-2", "ds-3"]


def test_sticky_rr_holds_head_for_limit_then_advances():
    registry = _three_account_registry()
    handler = FallbackHandler(registry)
    firsts = [
        handler.resolve_attempts("ds/m1", strategy=AccountStrategy.STICKY_RR, sticky_limit=2)[
            0
        ].account_id
        for _ in range(5)
    ]
    assert firsts == ["ds-1", "ds-1", "ds-2", "ds-2", "ds-3"]


def test_client_key_sticky_override_takes_precedence_over_strategy():
    registry = _three_account_registry()
    handler = FallbackHandler(registry)
    first = handler.resolve_attempts(
        "ds/m1",
        strategy=AccountStrategy.FILL_FIRST,
        client_key_id=1,
        sticky_client_key=True,
    )
    second = handler.resolve_attempts(
        "ds/m1",
        strategy=AccountStrategy.FILL_FIRST,
        client_key_id=1,
        sticky_client_key=True,
    )
    assert first[0].account_id == second[0].account_id == "ds-2"

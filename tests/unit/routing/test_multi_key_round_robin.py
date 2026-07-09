from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import AccountStrategy, FallbackHandler
from janus.routing.upstream_expand import expand_gateway_provider


def _expand_and_register(prefix: str, key_ids: list[str]) -> ProviderRegistry:
    row = {
        "id": prefix,
        "prefix": prefix,
        "api_type": "openai_compat",
        "base_url": f"https://{prefix}.example/v1",
        "api_key": None,
        "models": '["m1"]',
        "quota_window": None,
        "quota_limit": None,
        "quota_metric": "requests",
        "transports": None,
    }
    keys = [
        {
            "id": kid,
            "key_value": f"sk-{kid}",
            "custom_base_url": None,
            "rate_limit_rpm": None,
            "rate_limit_rpd": None,
        }
        for kid in key_ids
    ]
    registry = ProviderRegistry()
    for pc in expand_gateway_provider(row, keys):
        registry.register(pc)
    return registry


def test_openrouter_deepseek_xai_inventory_keys_round_robin() -> None:
    cases = {
        "deepseek": ["ds-a", "ds-b", "ds-c"],
        "openrouter": ["or-a", "or-b", "or-c", "or-d"],
        "xai": ["xai-a", "xai-b"],
    }
    for prefix, key_ids in cases.items():
        handler = FallbackHandler(_expand_and_register(prefix, key_ids))
        # sticky client-key staggers start (2 % n) then RR still covers every key.
        phase = 2 % len(key_ids)
        expected = key_ids[phase:] + key_ids[:phase]
        firsts = [
            handler.resolve_attempts(
                f"{prefix}/m1",
                strategy=AccountStrategy.ROUND_ROBIN,
                client_key_id=2,
                sticky_client_key=True,
            )[0].account_id
            for _ in range(len(key_ids) + 1)
        ]
        assert firsts[: len(key_ids)] == expected
        assert firsts[-1] == expected[0]
        assert set(firsts[: len(key_ids)]) == set(key_ids)


def test_reload_preserves_rotation_counters() -> None:
    registry = ProviderRegistry()
    for account_id in ("a1", "a2", "a3"):
        registry.register(
            ProviderConfig(
                id=account_id,
                prefix="p",
                api_type="openai_compat",
                base_url="https://p.example",
                api_key="k",
                models=["m"],
            )
        )
    old = FallbackHandler(registry)
    old.resolve_attempts("p/m", strategy=AccountStrategy.ROUND_ROBIN)
    old.resolve_attempts("p/m", strategy=AccountStrategy.ROUND_ROBIN)
    # After two advances, next head should be a3.
    new = FallbackHandler(registry)
    new.adopt_runtime_state(old)
    nxt = new.resolve_attempts("p/m", strategy=AccountStrategy.ROUND_ROBIN)[0].account_id
    assert nxt == "a3"

import time

import pytest

from janus.config.schema import ComboConfig, ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import AccountStrategy, FallbackHandler


def test_resolve_simple():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="glm",
            prefix="glm",
            api_type="openai_compat",
            base_url="https://test.com",
            api_key="sk",
            models=["glm-4.7"],
        )
    )
    result = registry.lookup("glm/glm-4.7")
    assert result is not None
    assert len(result) == 1
    assert result[0].model == "glm-4.7"
    assert result[0].native_format == "openai"


def test_resolve_unknown():
    registry = ProviderRegistry()
    assert registry.lookup("no/model") is None


def test_resolve_single_model_multi_account():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="ds-1",
            prefix="ds",
            api_type="openai_compat",
            base_url="https://ds.com",
            api_key="k1",
            models=["m1"],
        )
    )
    registry.register(
        ProviderConfig(
            id="ds-2",
            prefix="ds",
            api_type="openai_compat",
            base_url="https://ds.com",
            api_key="k2",
            models=["m1"],
        )
    )
    handler = FallbackHandler(registry)
    attempts = handler.resolve_attempts("ds/m1")
    assert len(attempts) == 2


def test_sticky_routing_is_consistent_per_client_key():
    registry = ProviderRegistry()
    for account_id in ("ds-1", "ds-2", "ds-3"):
        registry.register(
            ProviderConfig(
                id=account_id,
                prefix="ds",
                api_type="openai_compat",
                base_url="https://ds.com",
                api_key=f"k-{account_id}",
                models=["m1"],
            )
        )
    handler = FallbackHandler(registry)

    # With default round_robin, sticky client-key only staggers the start offset
    # and still advances each request.
    first = handler.resolve_attempts("ds/m1", client_key_id=7, sticky_client_key=True)
    second = handler.resolve_attempts("ds/m1", client_key_id=7, sticky_client_key=True)

    assert [t.account_id for t in first] == ["ds-2", "ds-3", "ds-1"]
    assert [t.account_id for t in second] == ["ds-3", "ds-1", "ds-2"]


def test_sticky_routing_without_client_key_uses_round_robin():
    registry = ProviderRegistry()
    for account_id in ("ds-1", "ds-2"):
        registry.register(
            ProviderConfig(
                id=account_id,
                prefix="ds",
                api_type="openai_compat",
                base_url="https://ds.com",
                api_key=f"k-{account_id}",
                models=["m1"],
            )
        )
    handler = FallbackHandler(registry)

    first = handler.resolve_attempts("ds/m1", sticky_client_key=True)
    second = handler.resolve_attempts("ds/m1", sticky_client_key=True)

    assert [t.account_id for t in first] == ["ds-1", "ds-2"]
    assert [t.account_id for t in second] == ["ds-2", "ds-1"]


def test_sticky_routing_different_client_keys_get_different_primaries():
    registry = ProviderRegistry()
    for account_id in ("ds-1", "ds-2", "ds-3"):
        registry.register(
            ProviderConfig(
                id=account_id,
                prefix="ds",
                api_type="openai_compat",
                base_url="https://ds.com",
                api_key=f"k-{account_id}",
                models=["m1"],
            )
        )
    handler = FallbackHandler(registry)

    a = handler.resolve_attempts(
        "ds/m1",
        client_key_id=1,
        sticky_client_key=True,
        strategy=AccountStrategy.FILL_FIRST,
    )
    b = handler.resolve_attempts(
        "ds/m1",
        client_key_id=2,
        sticky_client_key=True,
        strategy=AccountStrategy.FILL_FIRST,
    )

    assert a[0].account_id == "ds-2"
    assert b[0].account_id == "ds-3"


def test_round_robin_rotates_account_order():
    registry = ProviderRegistry()
    for account_id in ("ds-1", "ds-2", "ds-3"):
        registry.register(
            ProviderConfig(
                id=account_id,
                prefix="ds",
                api_type="openai_compat",
                base_url="https://ds.com",
                api_key=f"k-{account_id}",
                models=["m1"],
            )
        )
    handler = FallbackHandler(registry)

    first = handler.resolve_attempts("ds/m1")
    second = handler.resolve_attempts("ds/m1")
    third = handler.resolve_attempts("ds/m1")

    assert [t.account_id for t in first] == ["ds-1", "ds-2", "ds-3"]
    assert [t.account_id for t in second] == ["ds-2", "ds-3", "ds-1"]
    assert [t.account_id for t in third] == ["ds-3", "ds-1", "ds-2"]


def test_round_robin_skips_cooled_down_accounts():
    registry = ProviderRegistry()
    for account_id in ("ds-1", "ds-2", "ds-3"):
        registry.register(
            ProviderConfig(
                id=account_id,
                prefix="ds",
                api_type="openai_compat",
                base_url="https://ds.com",
                api_key=f"k-{account_id}",
                models=["m1"],
            )
        )
    handler = FallbackHandler(registry)
    handler.mark_cooldown("ds-2", "rate_limit")

    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["ds-1", "ds-3"]

    attempts = handler.resolve_attempts("ds/m1")
    assert [t.account_id for t in attempts] == ["ds-3", "ds-1"]


def test_round_robin_rotates_within_combo_model_groups():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="a-1",
            prefix="a",
            api_type="openai_compat",
            base_url="https://a.com",
            api_key="k1",
            models=["b"],
        )
    )
    registry.register(
        ProviderConfig(
            id="a-2",
            prefix="a",
            api_type="openai_compat",
            base_url="https://a.com",
            api_key="k2",
            models=["b"],
        )
    )
    registry.register(
        ProviderConfig(
            id="c",
            prefix="c",
            api_type="anthropic",
            base_url="https://c.com",
            api_key="k",
            models=["d"],
        )
    )
    registry.register_combo(ComboConfig(name="stk", models=["a/b", "c/d"]))
    handler = FallbackHandler(registry)

    first = handler.resolve_attempts("stk")
    second = handler.resolve_attempts("stk")

    assert [t.account_id for t in first] == ["a-1", "a-2", "c"]
    assert [t.account_id for t in second] == ["a-2", "a-1", "c"]


def test_resolve_combo_expansion():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="a",
            prefix="a",
            api_type="openai_compat",
            base_url="https://a.com",
            api_key="k",
            models=["b"],
        )
    )
    registry.register(
        ProviderConfig(
            id="c",
            prefix="c",
            api_type="anthropic",
            base_url="https://c.com",
            api_key="k",
            models=["d"],
        )
    )
    registry.register_combo(ComboConfig(name="stk", models=["a/b", "c/d"]))
    handler = FallbackHandler(registry)
    attempts = handler.resolve_attempts("stk")
    assert len(attempts) == 2
    assert attempts[0].model == "b"
    assert attempts[1].model == "d"


def test_cooldown_filters_account():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="ds-1",
            prefix="ds",
            api_type="openai_compat",
            base_url="https://ds.com",
            api_key="k1",
            models=["m1"],
        )
    )
    registry.register(
        ProviderConfig(
            id="ds-2",
            prefix="ds",
            api_type="openai_compat",
            base_url="https://ds.com",
            api_key="k2",
            models=["m1"],
        )
    )
    handler = FallbackHandler(registry)
    handler.mark_cooldown("ds-1", "rate_limit")
    attempts = handler.resolve_attempts("ds/m1")
    assert len(attempts) == 1
    assert attempts[0].account_id == "ds-2"


def test_cooldown_expiry():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="x",
            prefix="x",
            api_type="openai_compat",
            base_url="https://x.com",
            api_key="k",
            models=["m"],
        )
    )
    handler = FallbackHandler(registry)
    handler.mark_cooldown("x", "network", duration=0.0)
    time.sleep(0.01)
    attempts = handler.resolve_attempts("x/m")
    assert len(attempts) == 1


def test_all_accounts_exhausted_raises():
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="x",
            prefix="x",
            api_type="openai_compat",
            base_url="https://x.com",
            api_key="k",
            models=["m"],
        )
    )
    handler = FallbackHandler(registry)
    handler.mark_cooldown("x", "rate_limit", duration=9999.0)
    with pytest.raises(ValueError, match="No available"):
        handler.resolve_attempts("x/m")


def test_unknown_model_raises():
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    with pytest.raises(ValueError, match="Unknown model"):
        handler.resolve_attempts("no/such")


def test_retry_after_override():
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    handler.mark_cooldown("x", "rate_limit", retry_after=120.0)
    assert not handler.is_available("x")


async def test_cooldown_persistence_round_trip(tmp_path):
    from janus.storage.database import init_db

    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="acct-a",
            prefix="p",
            api_type="openai_compat",
            base_url="https://p.com",
            api_key="k",
            models=["m"],
        )
    )
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    handler = FallbackHandler(registry, db_path=db_path)
    handler.mark_cooldown("acct-a", "rate_limit", duration=120.0)
    assert not handler.is_available("acct-a")

    import asyncio

    await asyncio.sleep(0.05)

    restored = FallbackHandler(registry, db_path=db_path)
    await restored.load_cooldowns()
    assert not restored.is_available("acct-a")


async def test_load_cooldowns_noop_without_db():
    registry = ProviderRegistry()
    handler = FallbackHandler(registry)
    await handler.load_cooldowns()
    assert handler.is_available("anything")

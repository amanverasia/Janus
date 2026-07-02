import time

import pytest

from janus.config.schema import ComboConfig, ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.routing.resolver import resolve


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
    result = resolve("glm/glm-4.7", registry)
    assert result is not None
    assert len(result) == 1
    assert result[0].model == "glm-4.7"
    assert result[0].native_format == "openai"


def test_resolve_unknown():
    registry = ProviderRegistry()
    assert resolve("no/model", registry) is None


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

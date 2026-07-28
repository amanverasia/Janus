import time

import pytest

from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import FallbackHandler
from janus.storage.cooldowns import get_active_cooldowns, save_cooldown
from janus.storage.database import init_db


def _handler(*, db_path=None) -> FallbackHandler:
    registry = ProviderRegistry()
    registry.register(
        ProviderConfig(
            id="a",
            prefix="p",
            api_type="openai_compat",
            base_url="https://x.com",
            api_key="k",
            models=["m"],
        )
    )
    return FallbackHandler(registry, db_path=db_path)


def test_disabled_ignores_existing_cooldown() -> None:
    h = _handler()
    h.mark_cooldown("a", "rate_limit", duration=60.0)
    assert h.is_available("a") is False
    h.cooldowns_enabled = False
    assert h.is_available("a") is True
    attempts = h.resolve_attempts("p/m")
    assert len(attempts) == 1


def test_disabled_mark_cooldown_noop() -> None:
    h = _handler()
    h.cooldowns_enabled = False
    h.mark_cooldown("a", "rate_limit", duration=60.0)
    assert h._cooldowns == {}
    h.cooldowns_enabled = True
    assert h.is_available("a") is True


def test_adopt_runtime_state_copies_cooldowns_enabled() -> None:
    old = _handler()
    old.cooldowns_enabled = False
    new = _handler()
    new.adopt_runtime_state(old)
    assert new.cooldowns_enabled is False


@pytest.mark.asyncio
async def test_clear_all_empties_memory_and_db(tmp_path) -> None:
    db = tmp_path / "j.db"
    await init_db(db)
    h = _handler(db_path=db)
    h.mark_cooldown("a", "rate_limit", duration=60.0)
    await save_cooldown(db, "b", expires_at=time.time() + 60)
    n = await h.clear_all_cooldowns()
    assert n >= 1
    assert h._cooldowns == {}
    assert await get_active_cooldowns(db) == {}

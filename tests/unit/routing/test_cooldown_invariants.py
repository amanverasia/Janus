from pathlib import Path
from unittest.mock import patch

import pytest

from janus.config.schema import ComboConfig, ProviderConfig
from janus.providers.base import parse_retry_after
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import AllAccountsCooledDown, FallbackHandler
from janus.storage.cooldowns import get_active_cooldowns, save_cooldown
from janus.storage.database import init_db


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch) -> FallbackHandler:
    monkeypatch.setattr("janus.routing.fallback.time.time", lambda: 1000.0)
    registry = ProviderRegistry()
    for account in ("alpha", "beta"):
        registry.register(
            ProviderConfig(
                id=account,
                prefix="provider",
                api_type="openai_compat",
                base_url="https://upstream.test/v1",
                models=["m1", "m2"],
            )
        )
    registry.register_combo(ComboConfig(name="combo", models=["provider/m1", "provider/m2"]))
    return FallbackHandler(registry)


@pytest.mark.parametrize("hint", [float("nan"), float("inf"), -float("inf"), -5.0])
@pytest.mark.parametrize("field", ["retry_after", "duration"])
def test_invalid_direct_hint_uses_error_class_cooldown(handler, hint, field):
    handler.mark_cooldown("alpha", "rate_limit", model="m1", **{field: hint})
    assert handler._cooldowns[("alpha", "m1")] == 1002.0
    assert handler._backoff[("alpha", "m1")] == 1
    assert not handler.is_available("alpha", "m1")


def test_invalid_duration_uses_valid_retry_after(handler):
    handler.mark_cooldown("alpha", "rate_limit", duration=-1, retry_after=90)
    assert handler._cooldowns[("alpha", "__all__")] == 1090.0


@pytest.mark.parametrize("raw", ["NaN", "inf", "1e999", "-5"])
def test_invalid_upstream_retry_hint_preserves_normal_backoff(handler, raw):
    handler.mark_cooldown(
        "alpha", "rate_limit", retry_after=parse_retry_after({"retry-after": raw})
    )
    assert handler._cooldowns[("alpha", "__all__")] == 1002.0


def test_retry_hint_cap_and_zero_are_preserved(handler):
    handler.mark_cooldown("alpha", "rate_limit", model="m1", retry_after=1e300)
    assert handler._cooldowns[("alpha", "m1")] == 2800.0
    handler.mark_cooldown("alpha", "rate_limit", model="m2", retry_after=0)
    assert handler.is_available("alpha", "m2")


@pytest.mark.parametrize("scope", [None, "m1"])
@pytest.mark.parametrize("hint", [None, 0.0, 30.0])
def test_late_failure_preserves_longer_active_cooldown(handler, scope, hint):
    handler.mark_cooldown("alpha", "rate_limit", model=scope, retry_after=600)
    handler.mark_cooldown("alpha", "server_error", model=scope, retry_after=hint)
    assert handler._cooldowns[("alpha", scope or "__all__")] == 1600.0


def test_retry_time_waits_for_both_scopes_then_picks_first_usable_account(handler):
    handler.mark_cooldown("alpha", "auth_error")
    handler.mark_cooldown("alpha", "server_error", model="m1")
    handler.mark_cooldown("beta", "network", duration=400)
    handler.mark_cooldown("beta", "server_error", model="m1", duration=60)
    assert handler.earliest_cooldown_expiry(["alpha"], "m1") == 1300.0
    assert handler.earliest_cooldown_expiry(["beta"], "m1") == 1400.0
    with pytest.raises(AllAccountsCooledDown) as exc_info:
        handler.resolve_attempts("provider/m1")
    assert exc_info.value.retry_after == 300.0
    with pytest.raises(AllAccountsCooledDown) as exc_info:
        handler.resolve_attempts("combo")
    assert exc_info.value.retry_after == 300.0


def test_available_alternate_has_no_wait(handler):
    handler.mark_cooldown("alpha", "auth_error")
    assert handler.earliest_cooldown_expiry(["alpha", "beta"], "m1") is None
    handler.mark_cooldown("beta", "auth_error")
    handler.cooldowns_enabled = False
    assert handler.earliest_cooldown_expiry(["alpha", "beta"], "m1") is None


async def test_adopted_cooldown_is_not_shortened_by_stale_database(tmp_path: Path):
    db = tmp_path / "cooldowns.db"
    await init_db(db)
    with patch("janus.routing.fallback.time.time", return_value=1000.0):
        await save_cooldown(db, "alpha", 1030.0, model="m1", backoff_level=1)
        old = FallbackHandler(ProviderRegistry())
        old.mark_cooldown("alpha", "rate_limit", model="m1", retry_after=600)
        replacement = FallbackHandler(ProviderRegistry(), db_path=db)
        replacement.adopt_runtime_state(old)
        await replacement.load_cooldowns()
        assert replacement._cooldowns[("alpha", "m1")] == 1600.0
        assert replacement._backoff[("alpha", "m1")] == 0


async def test_cooldown_persists_reloads_and_clears_by_scope(tmp_path: Path):
    db = tmp_path / "cooldowns.db"
    await init_db(db)
    with patch("janus.routing.fallback.time.time", return_value=1000.0):
        handler = FallbackHandler(ProviderRegistry(), db_path=db)
        handler.mark_cooldown("alpha", "rate_limit", model="m1", retry_after=600)
        await handler._drain_persist_tasks()
        handler.mark_cooldown("alpha", "server_error", model="m1")
        handler.mark_cooldown("alpha", "auth_error")
        await handler._drain_persist_tasks()
        replacement = FallbackHandler(ProviderRegistry(), db_path=db)
        await replacement.load_cooldowns()
        assert replacement._cooldowns[("alpha", "m1")] == 1600.0
        assert replacement.earliest_cooldown_expiry(["alpha"], "m1") == 1600.0
        replacement.mark_success("alpha", "m1")
        await replacement._drain_persist_tasks()
        active = await get_active_cooldowns(db)
        assert active == {"alpha::__all__": (1300.0, 0)}
        assert not replacement.is_available("alpha", "m1")
        assert await replacement.clear_all_cooldowns() == 1
        assert await get_active_cooldowns(db) == {}

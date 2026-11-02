import time
from pathlib import Path

import pytest

from janus.config.schema import ProviderConfig
from janus.providers.registry import ProviderRegistry
from janus.routing.fallback import (
    ROUTING_HEALTH_FAILURE_THRESHOLD,
    AccountStrategy,
    FallbackHandler,
)
from janus.storage.database import get_connection, init_db
from janus.storage.routing_health import (
    RoutingHealth,
    clear_routing_unhealthy,
    load_routing_health,
    mark_routing_unhealthy,
)
from janus.storage.upstream_keys import create_upstream_key, list_upstream_key_history


def _config(key_id: str) -> ProviderConfig:
    return ProviderConfig(
        id=f"prov::{key_id}",
        prefix="test",
        api_type="openai_compat",
        base_url="https://fake.local/v1",
        api_key="sk-x",
        models=["m1"],
        upstream_key_id=key_id,
    )


def _handler(*key_ids: str, db_path: Path | None = None) -> FallbackHandler:
    registry = ProviderRegistry()
    for key_id in key_ids:
        registry.register(_config(key_id))
    return FallbackHandler(registry, db_path=db_path)


def _order(handler: FallbackHandler) -> list[str]:
    attempts = handler.resolve_attempts("test/m1", strategy=AccountStrategy.FILL_FIRST)
    return [target.account_id for target in attempts]


def _fail(handler: FallbackHandler, account_id: str, error_type: str, times: int, **kw) -> None:
    for _ in range(times):
        handler.mark_cooldown(account_id, error_type, model="m1", **kw)


@pytest.mark.parametrize("error_type,status", [("payment_error", 402), ("auth_error", 401)])
def test_threshold_flags_credential_and_records_status(error_type, status):
    handler = _handler("uk_a", "uk_b")
    _fail(handler, "uk_a", error_type, ROUTING_HEALTH_FAILURE_THRESHOLD - 1, status=status)
    assert not handler.is_routing_unhealthy("uk_a")
    _fail(handler, "uk_a", error_type, 1, status=status)
    health = handler.routing_health("uk_a")
    assert health is not None
    assert health.reason == error_type
    assert health.status_code == status
    assert health.at <= time.time()
    assert not handler.is_routing_unhealthy("uk_b")


def test_payment_error_defaults_to_402_status():
    handler = _handler("uk_a")
    _fail(handler, "uk_a", "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    health = handler.routing_health("uk_a")
    assert health is not None and health.status_code == 402


@pytest.mark.parametrize("error_type", ["rate_limit", "server_error", "network", "client_error"])
def test_non_credential_failures_never_count(error_type):
    handler = _handler("uk_a")
    _fail(handler, "uk_a", error_type, ROUTING_HEALTH_FAILURE_THRESHOLD * 5)
    assert not handler.is_routing_unhealthy("uk_a")


def test_interleaved_transient_errors_do_not_reset_or_count():
    handler = _handler("uk_a")
    _fail(handler, "uk_a", "payment_error", 2)
    _fail(handler, "uk_a", "rate_limit", 4)
    _fail(handler, "uk_a", "server_error", 4)
    assert not handler.is_routing_unhealthy("uk_a")
    _fail(handler, "uk_a", "payment_error", 1)
    assert handler.is_routing_unhealthy("uk_a")


def test_success_resets_streak_before_threshold():
    handler = _handler("uk_a")
    _fail(handler, "uk_a", "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD - 1)
    handler.mark_success("uk_a", "m1")
    _fail(handler, "uk_a", "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD - 1)
    assert not handler.is_routing_unhealthy("uk_a")


def test_success_clears_flagged_credential():
    handler = _handler("uk_a")
    _fail(handler, "uk_a", "auth_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    assert handler.is_routing_unhealthy("uk_a")
    handler.mark_success("uk_a", "m1")
    assert not handler.is_routing_unhealthy("uk_a")


def test_unhealthy_demoted_with_cooldowns_disabled_and_never_blocked():
    handler = _handler("uk_dead", "uk_ok1", "uk_ok2")
    handler.cooldowns_enabled = False
    _fail(handler, "uk_dead", "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    assert _order(handler) == ["uk_ok1", "uk_ok2", "uk_dead"]
    assert len(handler.resolve_attempts("test/m1")) == 3


def test_all_unhealthy_still_routable():
    handler = _handler("uk_a", "uk_b")
    handler.cooldowns_enabled = False
    _fail(handler, "uk_a", "auth_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    _fail(handler, "uk_b", "auth_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    assert sorted(_order(handler)) == ["uk_a", "uk_b"]


def test_adopt_runtime_state_keeps_flags_and_streaks():
    old = _handler("uk_a", "uk_b")
    _fail(old, "uk_a", "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    _fail(old, "uk_b", "payment_error", 2)
    new = _handler("uk_a", "uk_b")
    new.adopt_runtime_state(old)
    assert new.is_routing_unhealthy("uk_a")
    _fail(new, "uk_b", "payment_error", 1)
    assert new.is_routing_unhealthy("uk_b")


async def _key(db: Path, label: str = "k") -> str:
    record = await create_upstream_key(db, provider_id="openai", key_value=f"sk-{label}-1234567")
    async with get_connection(db) as conn:
        await conn.execute(
            "UPDATE upstream_keys SET status='active', is_valid=1, is_usable=1 WHERE id=?",
            (record["id"],),
        )
        await conn.commit()
    return str(record["id"])


async def test_flag_persists_and_reload_adopts_it(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    key_id = await _key(db)
    handler = _handler(key_id, db_path=db)
    _fail(handler, key_id, "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD, status=402)
    await handler._drain_persist_tasks()

    stored = await load_routing_health(db)
    assert stored[key_id].reason == "payment_error"
    assert stored[key_id].status_code == 402

    restarted = _handler(key_id, db_path=db)
    await restarted.load_routing_health()
    assert restarted.is_routing_unhealthy(key_id)


async def test_success_clears_persisted_flag(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    key_id = await _key(db)
    handler = _handler(key_id, db_path=db)
    _fail(handler, key_id, "auth_error", ROUTING_HEALTH_FAILURE_THRESHOLD, status=401)
    await handler._drain_persist_tasks()
    handler.mark_success(key_id, "m1")
    await handler._drain_persist_tasks()
    assert await load_routing_health(db) == {}


async def test_load_drops_stale_memory_flag_cleared_in_db(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    key_id = await _key(db)
    handler = _handler(key_id, db_path=db)
    _fail(handler, key_id, "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    await handler._drain_persist_tasks()
    await clear_routing_unhealthy(db, key_id)
    handler._unhealthy[key_id] = RoutingHealth(
        reason="payment_error", status_code=402, at=time.time() - 3600
    )
    await handler.load_routing_health()
    assert not handler.is_routing_unhealthy(key_id)


async def test_load_keeps_unflushed_recent_memory_flag(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    key_id = await _key(db)
    handler = _handler(key_id, db_path=db)
    handler.cooldowns_enabled = False
    handler.db_path = None
    _fail(handler, key_id, "payment_error", ROUTING_HEALTH_FAILURE_THRESHOLD)
    handler.db_path = db
    await handler.load_routing_health()
    assert handler.is_routing_unhealthy(key_id)


async def test_history_only_on_transitions(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    key_id = await _key(db)
    assert await mark_routing_unhealthy(
        db, key_id, reason="payment_error", status_code=402, at=time.time()
    )
    assert not await mark_routing_unhealthy(
        db, key_id, reason="payment_error", status_code=402, at=time.time()
    )
    history = await list_upstream_key_history(db, key_id)
    assert [(h["previous_status"], h["new_status"]) for h in history] == [
        ("active", "routing_unhealthy")
    ]
    assert await clear_routing_unhealthy(db, key_id)
    assert not await clear_routing_unhealthy(db, key_id)
    history = await list_upstream_key_history(db, key_id)
    assert len(history) == 2
    assert {(h["previous_status"], h["new_status"]) for h in history} == {
        ("active", "routing_unhealthy"),
        ("routing_unhealthy", "active"),
    }


async def test_mark_unknown_key_is_noop(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    assert not await mark_routing_unhealthy(
        db, "missing", reason="auth_error", status_code=401, at=time.time()
    )

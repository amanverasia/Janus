import time

import pytest

import janus.storage.usage as usage_module
from janus.pricing.registry import PricingRegistry
from janus.storage.database import get_connection, init_db
from janus.storage.settings import resolve_usage_retention_days
from janus.storage.usage import (
    UNPRICED_MODELS_CACHE_TTL_S,
    _last_retention_prune,
    backfill_costs,
    get_unpriced_models,
    invalidate_unpriced_models_cache,
    prune_usage_rows,
    record_usage,
)
from tests.fixtures.usage_seed import seed_usage

OLD_TS = "2020-01-01 00:00:00"


@pytest.fixture(autouse=True)
def reset_maintenance_state():
    _last_retention_prune.clear()
    invalidate_unpriced_models_cache()
    yield
    _last_retention_prune.clear()
    invalidate_unpriced_models_cache()


def test_resolve_usage_retention_default():
    assert resolve_usage_retention_days({}) == 365


def test_resolve_usage_retention_clamp():
    assert resolve_usage_retention_days({"server_usage_retention_days": "3"}) == 7
    assert resolve_usage_retention_days({"server_usage_retention_days": "99999"}) == 3650
    assert resolve_usage_retention_days({"server_usage_retention_days": "30"}) == 30
    assert resolve_usage_retention_days({"server_usage_retention_days": "abc"}) == 365


async def test_prune_usage_rows_deletes_only_expired_rows(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"model": "old-model", "timestamp": OLD_TS},
            {"model": "new-model"},
        ],
    )
    deleted = await prune_usage_rows(db_path, 365)
    assert deleted == 1
    async with get_connection(db_path) as db:
        async with db.execute("SELECT model FROM usage") as cur:
            models = {row["model"] for row in await cur.fetchall()}
    assert models == {"new-model"}


async def test_record_usage_prunes_expired_usage_and_history(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(db_path, [{"model": "old-model", "timestamp": OLD_TS}])
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO upstream_key_history
               (upstream_key_id, previous_status, new_status, changed_at)
               VALUES ('k-ancient', 'active', 'invalid', ?)""",
            (OLD_TS,),
        )
        await db.commit()

    await record_usage(db_path, model="new-model", input_tokens=5)

    async with get_connection(db_path) as db:
        async with db.execute("SELECT model FROM usage") as cur:
            models = {row["model"] for row in await cur.fetchall()}
        async with db.execute("SELECT COUNT(*) FROM upstream_key_history") as cur:
            history_count = (await cur.fetchone())[0]
    assert models == {"new-model"}
    assert history_count == 0


async def test_record_usage_retention_prune_is_throttled(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(db_path, model="first", input_tokens=1)
    await seed_usage(db_path, [{"model": "old-model", "timestamp": OLD_TS}])

    await record_usage(db_path, model="second", input_tokens=1)

    async with get_connection(db_path) as db:
        async with db.execute("SELECT model FROM usage") as cur:
            models = {row["model"] for row in await cur.fetchall()}
    assert models == {"first", "second", "old-model"}


async def test_prune_failure_does_not_break_recording(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)

    async def boom(db, retention_days):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(usage_module, "prune_usage_rows", boom)
    await record_usage(db_path, model="kept", input_tokens=1)
    async with get_connection(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM usage") as cur:
            assert (await cur.fetchone())[0] == 1


async def test_get_unpriced_models_cached_until_invalidated(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(db_path, [{"model": "mystery-model", "input_tokens": 100}])
    calls = 0
    real = usage_module._compute_unpriced_models

    async def counting(p, days):
        nonlocal calls
        calls += 1
        return await real(p, days)

    monkeypatch.setattr(usage_module, "_compute_unpriced_models", counting)

    first = await get_unpriced_models(db_path)
    second = await get_unpriced_models(db_path)
    assert calls == 1
    assert [row["model"] for row in first] == ["mystery-model"]
    assert first == second

    invalidate_unpriced_models_cache(db_path)
    await get_unpriced_models(db_path)
    assert calls == 2


async def test_unpriced_models_cache_expires_after_ttl(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(db_path, [{"model": "mystery-model", "input_tokens": 100}])
    calls = 0
    real = usage_module._compute_unpriced_models

    async def counting(p, days):
        nonlocal calls
        calls += 1
        return await real(p, days)

    monkeypatch.setattr(usage_module, "_compute_unpriced_models", counting)

    class _ShiftedClock:
        def __init__(self) -> None:
            self.offset = 0.0

        def monotonic(self) -> float:
            return time.monotonic() + self.offset

    clock = _ShiftedClock()
    monkeypatch.setattr(usage_module, "time", clock)

    await get_unpriced_models(db_path)
    await get_unpriced_models(db_path)
    assert calls == 1
    clock.offset = 3600.0
    await get_unpriced_models(db_path)
    assert calls == 2
    assert UNPRICED_MODELS_CACHE_TTL_S == 60.0


async def test_unpriced_models_cache_keyed_by_days_window(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(db_path, [{"model": "mystery-model", "input_tokens": 100}])
    calls = 0
    real = usage_module._compute_unpriced_models

    async def counting(p, days):
        nonlocal calls
        calls += 1
        return await real(p, days)

    monkeypatch.setattr(usage_module, "_compute_unpriced_models", counting)

    await get_unpriced_models(db_path, days=30)
    await get_unpriced_models(db_path, days=30)
    await get_unpriced_models(db_path, days=7)
    assert calls == 2


async def test_backfill_costs_invalidates_unpriced_cache(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [{"model": "mystery-model", "provider_id": "p1", "input_tokens": 100}],
    )
    registry = PricingRegistry(
        {},
        {
            "mystery-model": {
                "input_per_mtok": 3.0,
                "output_per_mtok": 15.0,
                "cache_creation_per_mtok": 0.0,
                "cache_read_per_mtok": 0.0,
            }
        },
    )

    before = await get_unpriced_models(db_path)
    assert [row["model"] for row in before] == ["mystery-model"]

    rows_updated, _added = await backfill_costs(db_path, registry)
    assert rows_updated == 1

    after = await get_unpriced_models(db_path)
    assert after == []

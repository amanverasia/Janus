import time

import pytest

from janus.storage.cooldowns import (
    _last_expired_prune,
    get_active_cooldowns,
    prune_expired_cooldowns,
    save_cooldown,
)
from janus.storage.database import get_connection, init_db


@pytest.fixture(autouse=True)
def reset_prune_throttle():
    _last_expired_prune.clear()
    yield
    _last_expired_prune.clear()


@pytest.fixture
async def db(tmp_path):
    p = tmp_path / "t.db"
    await init_db(p)
    return p


async def test_save_and_get_per_model(db):
    exp = time.time() + 100
    await save_cooldown(db, "acct-a", exp, model="gpt-4o", error_type="rate_limit", backoff_level=2)
    active = await get_active_cooldowns(db)
    assert "acct-a::gpt-4o" in active
    got_exp, got_level = active["acct-a::gpt-4o"]
    assert abs(got_exp - exp) < 0.01
    assert got_level == 2


async def test_default_model_is_all(db):
    await save_cooldown(db, "acct-b", time.time() + 100)
    active = await get_active_cooldowns(db)
    assert "acct-b::__all__" in active


async def test_expired_pruned(db):
    await save_cooldown(db, "acct-c", time.time() - 5, model="m")
    active = await get_active_cooldowns(db)
    assert "acct-c::m" not in active


async def test_same_account_two_models(db):
    await save_cooldown(db, "acct-d", time.time() + 100, model="m1")
    await save_cooldown(db, "acct-d", time.time() + 100, model="m2")
    active = await get_active_cooldowns(db)
    assert "acct-d::m1" in active and "acct-d::m2" in active


async def test_get_active_cooldowns_is_read_only(db):
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at) VALUES ('acct-e', 'm', ?)",
            (time.time() - 5,),
        )
        await conn.commit()
    active = await get_active_cooldowns(db)
    assert active == {}
    async with get_connection(db) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE account_id = 'acct-e'"
        ) as cur:
            assert (await cur.fetchone())[0] == 1


async def test_save_cooldown_prunes_expired_rows_opportunistically(db):
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at) VALUES ('stale', 'm', ?)",
            (time.time() - 5,),
        )
        await conn.commit()

    await save_cooldown(db, "acct-f", time.time() + 100)

    async with get_connection(db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM cooldowns WHERE account_id = 'stale'") as cur:
            assert (await cur.fetchone())[0] == 0


async def test_expired_prune_on_write_is_throttled(db):
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at) VALUES ('stale', 'm', ?)",
            (time.time() - 5,),
        )
        await conn.commit()
    await save_cooldown(db, "acct-g", time.time() + 100)
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at) VALUES ('stale2', 'm', ?)",
            (time.time() - 5,),
        )
        await conn.commit()

    await save_cooldown(db, "acct-h", time.time() + 100)

    async with get_connection(db) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM cooldowns WHERE account_id = 'stale2'"
        ) as cur:
            assert (await cur.fetchone())[0] == 1


async def test_prune_expired_cooldowns_returns_rowcount(db):
    await save_cooldown(db, "acct-j", time.time() + 100, model="m")
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at) VALUES ('acct-i', 'm', ?)",
            (time.time() - 5,),
        )
        await conn.commit()
    deleted = await prune_expired_cooldowns(db)
    assert deleted == 1
    active = await get_active_cooldowns(db)
    assert set(active) == {"acct-j::m"}

import time

import pytest

from janus.storage.cooldowns import get_active_cooldowns, save_cooldown
from janus.storage.database import init_db


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_save_and_get_cooldown(db):
    expires = time.time() + 60.0
    await save_cooldown(db, "acct-1", expires)
    result = await get_active_cooldowns(db)
    assert result == {"acct-1": expires}


async def test_get_active_cooldowns_filters_expired(db):
    await save_cooldown(db, "expired", time.time() - 10.0)
    await save_cooldown(db, "active", time.time() + 60.0)
    result = await get_active_cooldowns(db)
    assert result == {"active": pytest.approx(time.time() + 60.0, abs=1.0)}


async def test_get_active_cooldowns_prunes_expired(db):
    await save_cooldown(db, "expired", time.time() - 10.0)
    await get_active_cooldowns(db)
    result = await get_active_cooldowns(db)
    assert result == {}


async def test_save_cooldown_upserts(db):
    await save_cooldown(db, "acct-1", time.time() + 30.0)
    new_expires = time.time() + 120.0
    await save_cooldown(db, "acct-1", new_expires)
    result = await get_active_cooldowns(db)
    assert result == {"acct-1": new_expires}


async def test_get_active_cooldowns_empty(db):
    assert await get_active_cooldowns(db) == {}

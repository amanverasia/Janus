import time

import pytest

from janus.storage.cooldowns import get_active_cooldowns, save_cooldown
from janus.storage.database import init_db


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

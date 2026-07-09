import pytest

from janus.storage.database import init_db
from janus.storage.settings import (
    get_all_settings,
    get_setting,
    invalidate_settings_cache,
    set_setting,
)


@pytest.fixture
async def db(tmp_path):
    p = tmp_path / "s.db"
    await init_db(p)
    return p


async def test_set_setting_invalidates_cache(db):
    await set_setting(db, "k", "v1")
    assert await get_setting(db, "k") == "v1"
    await set_setting(db, "k", "v2")  # must invalidate the cached snapshot
    assert await get_setting(db, "k") == "v2"


async def test_get_all_settings_reflects_writes(db):
    await set_setting(db, "a", "1")
    first = await get_all_settings(db)
    assert first["a"] == "1"
    await set_setting(db, "b", "2")
    second = await get_all_settings(db)
    assert second["a"] == "1" and second["b"] == "2"


async def test_manual_invalidation(db):
    await set_setting(db, "k", "v")
    await get_all_settings(db)  # populate cache
    invalidate_settings_cache(db)
    # still readable from DB after invalidation
    assert await get_setting(db, "k") == "v"

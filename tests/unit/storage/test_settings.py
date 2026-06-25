import pytest

from janus.storage.database import init_db
from janus.storage.settings import get_all_settings, get_setting, set_setting


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_set_and_get_setting(db):
    await set_setting(db, "foo", "bar")
    assert await get_setting(db, "foo") == "bar"


async def test_get_setting_default(db):
    assert await get_setting(db, "nonexistent", "default_val") == "default_val"


async def test_get_setting_none_if_no_default(db):
    assert await get_setting(db, "nonexistent") is None


async def test_set_setting_overwrites(db):
    await set_setting(db, "key", "v1")
    await set_setting(db, "key", "v2")
    assert await get_setting(db, "key") == "v2"


async def test_get_all_settings_empty(db):
    assert await get_all_settings(db) == {}


async def test_get_all_settings(db):
    await set_setting(db, "a", "1")
    await set_setting(db, "b", "2")
    result = await get_all_settings(db)
    assert result == {"a": "1", "b": "2"}

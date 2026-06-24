import pytest

from janus.storage.database import get_connection, init_db


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = [row[0] for row in await cur.fetchall()]
    assert "api_keys" in tables
    assert "usage" in tables


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await init_db(db_path)


@pytest.mark.asyncio
async def test_init_db_creates_parent_dir(tmp_path):
    db_path = tmp_path / "subdir" / "nested" / "test.db"
    await init_db(db_path)
    assert db_path.exists()

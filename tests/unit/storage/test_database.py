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
    assert "inventory_providers" in tables
    assert "upstream_keys" in tables


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


@pytest.mark.asyncio
async def test_providers_table_has_allowed_models_column(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(providers)")
        rows = await cursor.fetchall()
    columns = {row[1]: row for row in rows}
    assert "allowed_models" in columns


@pytest.mark.asyncio
async def test_migration_adds_allowed_models_column_to_existing_db(tmp_path):
    import aiosqlite

    db_path = tmp_path / "test.db"
    await init_db(db_path)
    # Simulate a pre-existing DB from before this column existed.
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("ALTER TABLE providers DROP COLUMN allowed_models")
        await db.commit()
    # Re-running init_db should add the column back idempotently.
    await init_db(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(providers)")
        rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    assert "allowed_models" in columns

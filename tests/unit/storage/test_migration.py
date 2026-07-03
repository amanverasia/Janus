import aiosqlite
import pytest

from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_usage_table_has_new_columns(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("PRAGMA table_info(usage)") as cur:
            rows = await cur.fetchall()
    columns = {row[1] for row in rows}
    assert "cost" in columns
    assert "cache_creation_tokens" in columns
    assert "cache_read_tokens" in columns
    assert "client_key_id" in columns
    assert "client_key_label" in columns


@pytest.mark.asyncio
async def test_budgets_table_exists(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("PRAGMA table_info(budgets)") as cur:
            rows = await cur.fetchall()
    columns = {row[1] for row in rows}
    assert "id" in columns
    assert "key_id" in columns
    assert "daily_limit" in columns
    assert "warn_pct" in columns
    assert "is_active" in columns
    assert "created_at" in columns


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("PRAGMA table_info(usage)") as cur:
            rows = await cur.fetchall()
    column_names = [row[1] for row in rows]
    assert column_names.count("cost") == 1
    assert column_names.count("cache_creation_tokens") == 1

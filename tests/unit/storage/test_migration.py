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
    assert "absolute_limit" in columns
    assert next(row for row in rows if row[1] == "daily_limit")[3] == 0
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


@pytest.mark.parametrize("has_absolute_column", [False, True])
async def test_budget_migration_preserves_legacy_rows_and_allows_absolute_only(
    tmp_path, has_absolute_column
):
    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """CREATE TABLE budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id INTEGER,
                daily_limit REAL NOT NULL,
                warn_pct REAL DEFAULT 80,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (key_id) REFERENCES api_keys(id)
            )"""
        )
        await db.execute(
            "INSERT INTO budgets (id, key_id, daily_limit, warn_pct, is_active, created_at) "
            "VALUES (7, NULL, 5.0, 75, 1, '2025-01-01 00:00:00'), "
            "(12, 1, 2.0, 90, 0, '2025-02-01 00:00:00')"
        )
        await db.execute("INSERT INTO budgets (id, daily_limit) VALUES (25, 1)")
        await db.execute("DELETE FROM budgets WHERE id = 25")
        if has_absolute_column:
            await db.execute("ALTER TABLE budgets ADD COLUMN absolute_limit REAL")
            await db.execute("UPDATE budgets SET absolute_limit = 10 WHERE id = 12")
        await db.commit()
    await init_db(db_path)
    await init_db(db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute(
            "SELECT id, key_id, daily_limit, warn_pct, is_active, created_at, absolute_limit "
            "FROM budgets ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        assert rows == [
            (7, None, 5.0, 75.0, 1, "2025-01-01 00:00:00", None),
            (12, 1, 2.0, 90.0, 0, "2025-02-01 00:00:00", 10.0 if has_absolute_column else None),
        ]
    from janus.storage.api_keys import create_key
    from janus.storage.budgets import create_or_update_budget, get_budgets

    _, key = await create_key(db_path, "new-key")
    budget_id = await create_or_update_budget(db_path, key_id=key["id"], absolute_limit=20)
    assert budget_id > 25
    budget = (await get_budgets(db_path))[-1]
    assert budget["daily_limit"] is None
    assert budget["absolute_limit"] == 20
    await init_db(db_path)
    assert (await get_budgets(db_path))[-1] == budget

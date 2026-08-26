import pytest

from janus.storage.database import get_connection, init_db
from janus.storage.settings import get_all_settings, set_setting


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
    assert "custom_models" in tables


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await init_db(db_path)


@pytest.mark.asyncio
async def test_init_db_removes_legacy_dashboard_auth_settings(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    legacy_settings = {
        "dashboard_username": "hett",
        "dashboard_password_hash": "legacy-password-hash",
        "dashboard_session_secret": "legacy-session-secret",
    }
    for key, value in legacy_settings.items():
        await set_setting(db_path, key, value)

    await init_db(db_path)

    settings = await get_all_settings(db_path)
    assert legacy_settings.keys().isdisjoint(settings)


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
async def test_providers_table_has_model_catalog_columns(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(providers)")
        rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    assert {"catalog_id", "default_model", "live_models", "selected_models"} <= columns


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


@pytest.mark.asyncio
async def test_init_db_adds_is_archived_column(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(upstream_keys)")
        rows = await cursor.fetchall()
    columns = {row[1]: row for row in rows}
    assert "is_archived" in columns
    assert columns["is_archived"][4] == "0"


@pytest.mark.asyncio
async def test_existing_keys_default_to_not_archived(tmp_path):
    import aiosqlite

    from janus.storage.upstream_keys import create_upstream_key, get_upstream_key

    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(db_path, provider_id="openai", key_value="sk-test")
    # Simulate a DB created before is_archived existed.
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("ALTER TABLE upstream_keys DROP COLUMN is_archived")
        await db.commit()
    await init_db(db_path)
    key = await get_upstream_key(db_path, record["id"])
    assert key is not None
    assert key["is_archived"] == 0

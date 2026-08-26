import json

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


@pytest.mark.asyncio
async def test_custom_model_migration_moves_legacy_rows_to_provider_prefix(tmp_path):
    import aiosqlite

    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(
            """
            CREATE TABLE providers (
                id TEXT PRIMARY KEY,
                prefix TEXT NOT NULL,
                api_type TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT,
                models TEXT NOT NULL DEFAULT '[]',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE custom_models (
                id TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                display_name TEXT,
                context_window INTEGER,
                max_output_tokens INTEGER,
                input_modalities TEXT NOT NULL DEFAULT '[]',
                reasoning_efforts TEXT NOT NULL DEFAULT '[]',
                capabilities TEXT NOT NULL DEFAULT '{}',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(provider_id, model_id)
            );
            INSERT INTO providers (id, prefix, api_type, base_url)
            VALUES
                ('account-a', 'shared', 'openai_compat', 'https://example.test/v1'),
                ('account-b', 'shared', 'openai_compat', 'https://example.test/v1'),
                ('account-c', 'shared', 'openai_compat', 'https://example.test/v1');
            INSERT INTO custom_models
                (id, provider_id, model_id, display_name, context_window, max_output_tokens,
                 input_modalities, reasoning_efforts, capabilities, is_enabled,
                 created_at, updated_at)
            VALUES
                ('custom-a', 'account-a', 'model-one', 'Disabled metadata', 32768, NULL,
                 '["audio"]', '[]', '{"audio": true}', 0,
                 '2024-01-01 00:00:00', '2026-01-01 00:00:00'),
                ('custom-b', 'account-b', 'model-one', 'Preferred display', NULL, NULL,
                 '["text"]', '["high"]', '{"tools": true}', 1,
                 '2024-02-01 00:00:00', '2025-02-01 00:00:00'),
                ('custom-c', 'account-c', 'model-one', NULL, 128000, 8192,
                 '["image"]', '[]', '{"vision": true, "tools": false}', 1,
                 '2024-03-01 00:00:00', '2025-01-01 00:00:00'),
                ('tie-z', 'account-a', 'model-two', NULL, NULL, NULL,
                 '[]', '[]', '{}', 1,
                 '2025-01-01 00:00:00', '2025-01-01 00:00:00'),
                ('tie-a', 'account-b', 'model-two', NULL, NULL, NULL,
                 '[]', '[]', '{}', 1,
                 '2025-01-01 00:00:00', '2025-01-01 00:00:00');
            """
        )
        await db.commit()

    await init_db(db_path)

    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT id, provider_id, provider_prefix, model_id, display_name,
                      context_window, max_output_tokens, input_modalities,
                      reasoning_efforts, capabilities, is_enabled, created_at, updated_at
               FROM custom_models ORDER BY model_id"""
        ) as cursor:
            rows = await cursor.fetchall()
        async with db.execute("PRAGMA index_list(custom_models)") as cursor:
            indexes = {str(row[1]) for row in await cursor.fetchall()}
    assert len(rows) == 2
    model_one = rows[0]
    model_two = rows[1]
    assert model_one["id"] == "custom-c"
    assert model_one["provider_id"] == "account-c"
    assert model_one["provider_prefix"] == "shared"
    assert model_one["model_id"] == "model-one"
    assert model_one["display_name"] == "Preferred display"
    assert model_one["context_window"] == 128000
    assert model_one["max_output_tokens"] == 8192
    assert set(json.loads(model_one["input_modalities"])) == {
        "audio",
        "image",
        "text",
    }
    assert json.loads(model_one["reasoning_efforts"]) == ["high"]
    assert json.loads(model_one["capabilities"]) == {
        "audio": True,
        "tools": False,
        "vision": True,
    }
    assert model_one["is_enabled"] == 1
    assert model_one["created_at"] == "2024-01-01 00:00:00"
    assert model_one["updated_at"] == "2026-01-01 00:00:00"
    assert model_two["id"] == "tie-a"
    assert "idx_custom_models_prefix_model" in indexes


@pytest.mark.asyncio
async def test_config_seed_deduplicates_legacy_custom_models_by_prefix(tmp_path):
    from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
    from janus.storage.custom_models import list_custom_models
    from janus.storage.database import seed_from_config

    db_path = tmp_path / "seed.db"
    config = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="account-a",
                prefix="shared",
                api_type="openai_compat",
                base_url="https://example.test/v1",
                custom_models=["custom-model"],
            ),
            ProviderConfig(
                id="account-b",
                prefix="shared",
                api_type="openai_compat",
                base_url="https://example.test/v1",
                custom_models=["custom-model"],
            ),
        ],
    )
    await init_db(db_path)

    await seed_from_config(db_path, config)

    models = await list_custom_models(db_path)
    assert [(model["provider_prefix"], model["model_id"]) for model in models] == [
        ("shared", "custom-model")
    ]

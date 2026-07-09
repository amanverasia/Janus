import pytest

from janus.storage.api_keys import (
    create_key,
    get_key_policy,
    list_keys,
    revoke_key,
    update_key,
    verify_key,
)
from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_create_key_returns_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(db_path, name="test-key")
    assert key.startswith("sk-janus-")
    assert len(key) == len("sk-janus-") + 32
    assert record["name"] == "test-key"
    assert record["can_login"] is True
    assert record["allowed_models"] is None


@pytest.mark.asyncio
async def test_verify_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(db_path, name="test")
    assert await verify_key(db_path, key) == record["id"]
    assert await verify_key(db_path, "sk-janus-wrong") is None
    assert await verify_key(db_path, "not-even-a-key") is None


@pytest.mark.asyncio
async def test_list_keys(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_key(db_path, name="key1")
    await create_key(db_path, name="key2")
    keys = await list_keys(db_path)
    assert len(keys) == 2


@pytest.mark.asyncio
async def test_revoke_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(db_path, name="test")
    await revoke_key(db_path, record["id"])
    assert await verify_key(db_path, key) is None


@pytest.mark.asyncio
async def test_create_key_hash_not_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, name="test")
    keys = await list_keys(db_path)
    assert key not in str(keys)


@pytest.mark.asyncio
async def test_verify_key_returns_id(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    raw_key, record = await create_key(db_path, name="test")
    result = await verify_key(db_path, raw_key)
    assert result == record["id"]


@pytest.mark.asyncio
async def test_verify_key_returns_none_for_invalid(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    result = await verify_key(db_path, "sk-janus-deadbeef")
    assert result is None


@pytest.mark.asyncio
async def test_verify_revoked_key_returns_none(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    raw_key, record = await create_key(db_path, name="test")
    await revoke_key(db_path, record["id"])
    result = await verify_key(db_path, raw_key)
    assert result is None


@pytest.mark.asyncio
async def test_create_key_with_scopes(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(
        db_path,
        name="api-only",
        can_login=False,
        allowed_models=["openai/*", "my-combo"],
    )
    policy = await get_key_policy(db_path, int(record["id"]))
    assert policy is not None
    assert policy["can_login"] is False
    assert policy["allowed_models"] == ["openai/*", "my-combo"]
    keys = await list_keys(db_path)
    assert keys[0]["can_login"] is False
    assert keys[0]["allowed_models"] == ["openai/*", "my-combo"]
    assert await verify_key(db_path, key) == record["id"]


@pytest.mark.asyncio
async def test_update_key_scopes(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    _, record = await create_key(db_path, name="x")
    kid = int(record["id"])
    assert await update_key(db_path, kid, can_login=False, allowed_models=["a/b"])
    policy = await get_key_policy(db_path, kid)
    assert policy is not None
    assert policy["can_login"] is False
    assert policy["allowed_models"] == ["a/b"]
    assert await update_key(db_path, kid, allowed_models=None)
    policy = await get_key_policy(db_path, kid)
    assert policy is not None
    assert policy["allowed_models"] is None


@pytest.mark.asyncio
async def test_api_key_column_migration(tmp_path):
    import aiosqlite

    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """CREATE TABLE api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                prefix TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        await db.execute(
            "INSERT INTO api_keys (name, key_hash, prefix) VALUES ('old', 'h', 'sk-janus-abcdef')"
        )
        await db.commit()
    await init_db(db_path)
    keys = await list_keys(db_path)
    assert len(keys) == 1
    assert keys[0]["can_login"] is True
    assert keys[0]["allowed_models"] is None

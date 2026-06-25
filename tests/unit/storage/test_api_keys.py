import pytest

from janus.storage.api_keys import create_key, list_keys, revoke_key, verify_key
from janus.storage.database import init_db


@pytest.mark.asyncio
async def test_create_key_returns_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, record = await create_key(db_path, name="test-key")
    assert key.startswith("sk-janus-")
    assert len(key) == len("sk-janus-") + 32
    assert record["name"] == "test-key"


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

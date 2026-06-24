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
    key, _ = await create_key(db_path, name="test")
    assert await verify_key(db_path, key) is True
    assert await verify_key(db_path, "sk-janus-wrong") is False
    assert await verify_key(db_path, "not-even-a-key") is False


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
    assert await verify_key(db_path, key) is False


@pytest.mark.asyncio
async def test_create_key_hash_not_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key, _ = await create_key(db_path, name="test")
    keys = await list_keys(db_path)
    assert key not in str(keys)

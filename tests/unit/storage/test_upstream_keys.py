import pytest

from janus.storage.database import init_db, seed_inventory_providers
from janus.storage.inventory_providers import get_inventory_provider, list_inventory_providers
from janus.storage.upstream_keys import (
    count_upstream_keys,
    create_upstream_key,
    delete_upstream_key,
    get_upstream_key,
    list_upstream_keys,
    list_upstream_keys_masked,
    record_upstream_key_history,
    update_upstream_key,
)


@pytest.mark.asyncio
async def test_init_db_creates_inventory_tables(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    providers = await list_inventory_providers(db_path)
    assert len(providers) == 29
    assert await get_inventory_provider(db_path, "openai") is not None


@pytest.mark.asyncio
async def test_seed_inventory_providers_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    first = await list_inventory_providers(db_path)
    await seed_inventory_providers(db_path)
    second = await list_inventory_providers(db_path)
    assert len(first) == len(second) == 29


@pytest.mark.asyncio
async def test_create_upstream_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-test-key-value",
        key_label="primary",
    )
    assert record["status"] == "pending_validation"
    assert record["key_masked"] == "sk-p****-value"

    stored = await get_upstream_key(db_path, record["id"])
    assert stored is not None
    assert stored["key_value"] == "sk-proj-test-key-value"


@pytest.mark.asyncio
async def test_list_upstream_keys_masked_hides_plaintext(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-secret")
    keys = await list_upstream_keys_masked(db_path)
    assert len(keys) == 1
    assert "key_value" not in keys[0]


@pytest.mark.asyncio
async def test_update_and_delete_upstream_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(db_path, provider_id="groq", key_value="gsk_test")
    await update_upstream_key(
        db_path,
        record["id"],
        {"status": "valid", "is_valid": 1, "credits_remaining": 12.5},
    )
    await record_upstream_key_history(
        db_path,
        upstream_key_id=record["id"],
        previous_status="pending_validation",
        new_status="valid",
        credits_remaining=12.5,
    )
    updated = await get_upstream_key(db_path, record["id"])
    assert updated is not None
    assert updated["status"] == "valid"
    assert updated["credits_remaining"] == 12.5

    await delete_upstream_key(db_path, record["id"])
    assert await get_upstream_key(db_path, record["id"]) is None
    assert await count_upstream_keys(db_path) == 0


@pytest.mark.asyncio
async def test_list_upstream_keys_filters(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-one")
    await create_upstream_key(db_path, provider_id="groq", key_value="gsk_two")
    openai_keys = await list_upstream_keys(db_path, provider_id="openai")
    assert len(openai_keys) == 1
    assert openai_keys[0]["provider_id"] == "openai"

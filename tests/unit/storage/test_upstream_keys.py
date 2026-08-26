import pytest

from janus.catalog import inventory_catalog_entries
from janus.storage.database import init_db, seed_inventory_providers
from janus.storage.inventory_providers import get_inventory_provider, list_inventory_providers
from janus.storage.upstream_keys import (
    archive_upstream_keys,
    count_upstream_keys,
    count_upstream_keys_filtered,
    create_upstream_key,
    delete_upstream_key,
    delete_upstream_keys,
    get_upstream_key,
    list_upstream_key_ids_filtered,
    list_upstream_keys,
    list_upstream_keys_masked,
    list_upstream_keys_page,
    record_upstream_key_history,
    update_upstream_key,
)


@pytest.mark.asyncio
async def test_init_db_creates_inventory_tables(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    providers = await list_inventory_providers(db_path)
    assert len(providers) == len(inventory_catalog_entries())
    assert await get_inventory_provider(db_path, "openai") is not None


@pytest.mark.asyncio
async def test_seed_inventory_providers_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    first = await list_inventory_providers(db_path)
    await seed_inventory_providers(db_path)
    second = await list_inventory_providers(db_path)
    assert len(first) == len(second) == len(inventory_catalog_entries())


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


@pytest.mark.asyncio
async def test_archive_and_restore_upstream_keys(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    a = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-a")
    b = await create_upstream_key(db_path, provider_id="groq", key_value="gsk_b")

    archived_count = await archive_upstream_keys(db_path, [a["id"], b["id"]])
    assert archived_count == 2

    archived = await get_upstream_key(db_path, a["id"])
    assert archived is not None
    assert archived["is_archived"] == 1

    default_keys = await list_upstream_keys_page(db_path)
    assert default_keys == []
    archived_keys = await list_upstream_keys_page(db_path, status="archived")
    assert {k["id"] for k in archived_keys} == {a["id"], b["id"]}

    assert await count_upstream_keys_filtered(db_path, status="archived") == 2

    restored = await archive_upstream_keys(db_path, [a["id"]], archived=False)
    assert restored == 1
    visible = await list_upstream_keys_page(db_path)
    assert [k["id"] for k in visible] == [a["id"]]


@pytest.mark.asyncio
async def test_list_upstream_key_ids_filtered_matches_filter(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    a = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-a")
    await create_upstream_key(db_path, provider_id="groq", key_value="gsk_b")

    ids = await list_upstream_key_ids_filtered(db_path, provider_id="openai")
    assert ids == [a["id"]]

    await archive_upstream_keys(db_path, [a["id"]])
    active_ids = await list_upstream_key_ids_filtered(db_path, provider_id="openai")
    assert active_ids == []
    all_ids = await list_upstream_key_ids_filtered(
        db_path, provider_id="openai", include_archived=True
    )
    assert all_ids == [a["id"]]


@pytest.mark.asyncio
async def test_delete_upstream_keys_cascades(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    a = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-a")
    b = await create_upstream_key(db_path, provider_id="groq", key_value="gsk_b")
    await record_upstream_key_history(
        db_path,
        upstream_key_id=a["id"],
        new_status="pending_validation",
    )

    count = await delete_upstream_keys(db_path, [a["id"], b["id"]])
    assert count == 2
    assert await get_upstream_key(db_path, a["id"]) is None
    assert await get_upstream_key(db_path, b["id"]) is None
    assert await count_upstream_keys(db_path) == 0


@pytest.mark.asyncio
async def test_list_upstream_keys_excludes_archived(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    a = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-a")
    b = await create_upstream_key(db_path, provider_id="groq", key_value="gsk_b")
    await archive_upstream_keys(db_path, [a["id"]])

    keys = await list_upstream_keys(db_path)
    assert [k["id"] for k in keys] == [b["id"]]

    keys_all = await list_upstream_keys(db_path, include_archived=True)
    assert len(keys_all) == 2

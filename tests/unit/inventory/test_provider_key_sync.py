import pytest

from janus.inventory.provider_key_sync import (
    backfill_provider_keys,
    delete_mirrored_provider_key,
    sync_provider_key,
)
from janus.storage.database import init_db
from janus.storage.inventory_providers import get_inventory_provider
from janus.storage.upstream_keys import (
    find_upstream_key_by_value,
    list_upstream_keys,
)


def _provider(
    *,
    id: str = "mycustom",
    prefix: str = "mycustom",
    base_url: str = "https://api.example.com/v1",
    api_key: str = "sk-example-key-1234567890abcdef",
) -> dict:
    return {"id": id, "prefix": prefix, "base_url": base_url, "api_key": api_key}


@pytest.mark.asyncio
async def test_sync_provider_key_custom_prefix_upserts_inventory_provider(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key_id = await sync_provider_key(db_path, provider=_provider(), schedule_recheck=False)
    assert key_id is not None

    inv = await get_inventory_provider(db_path, "mycustom")
    assert inv is not None
    assert inv["display_name"] == "mycustom"
    assert inv["base_url"] == "https://api.example.com/v1"

    keys = await list_upstream_keys(db_path)
    assert len(keys) == 1
    assert keys[0]["provider_id"] == "mycustom"
    assert keys[0]["source_node"] == "gateway:mycustom"
    assert keys[0]["key_label"] == "via Providers: mycustom"


@pytest.mark.asyncio
async def test_sync_provider_key_known_catalog_prefix(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    provider = _provider(
        id="openai-gw",
        prefix="openai",
        api_key="sk-known-catalog-key-1234",
    )
    key_id = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert key_id is not None

    keys = await list_upstream_keys(db_path)
    assert len(keys) == 1
    assert keys[0]["provider_id"] == "openai"


@pytest.mark.asyncio
async def test_sync_gateway_only_preset_has_persisted_inventory_provider(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    provider = _provider(
        id="deepinfra-gw",
        prefix="deepinfra",
        base_url="https://api.deepinfra.com/v1/openai",
        api_key="deepinfra-key-1234567890",
    )

    key_id = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)

    assert key_id is not None
    inventory_provider = await get_inventory_provider(db_path, "deepinfra")
    assert inventory_provider is not None
    assert inventory_provider["models_endpoint"] == "/models"
    keys = await list_upstream_keys(db_path, provider_id="deepinfra")
    assert [key["id"] for key in keys] == [key_id]


@pytest.mark.asyncio
async def test_sync_provider_key_unchanged_skips(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    provider = _provider()
    first = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert first is not None

    second = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert second == first

    keys = await list_upstream_keys(db_path)
    assert len(keys) == 1


@pytest.mark.asyncio
async def test_sync_provider_key_changed_updates(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    provider = _provider(api_key="sk-original-key-1234567890")
    first = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert first is not None

    provider["api_key"] = "sk-rotated-key-9999999999"
    second = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert second == first

    keys = await list_upstream_keys(db_path)
    assert len(keys) == 1
    assert keys[0]["status"] == "pending_validation"


@pytest.mark.asyncio
async def test_sync_provider_key_no_key_revokes_existing(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    provider = _provider()
    await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert len(await list_upstream_keys(db_path)) == 1

    provider["api_key"] = None
    await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    keys = await list_upstream_keys(db_path)
    assert keys == []


@pytest.mark.asyncio
async def test_sync_provider_key_skips_when_already_in_inventory(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    from janus.storage.upstream_keys import create_upstream_key

    await create_upstream_key(db_path, provider_id="openai", key_value="sk-already-present-1234")

    provider = _provider(
        id="openai-gw",
        prefix="openai",
        api_key="sk-already-present-1234",
    )
    key_id = await sync_provider_key(db_path, provider=provider, schedule_recheck=False)
    assert key_id is None

    keys = await list_upstream_keys(db_path)
    assert len(keys) == 1
    assert keys[0]["source_node"] is None


@pytest.mark.asyncio
async def test_delete_mirrored_provider_key(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await sync_provider_key(db_path, provider=_provider(), schedule_recheck=False)
    assert len(await list_upstream_keys(db_path)) == 1

    await delete_mirrored_provider_key(db_path, "mycustom")
    keys = await list_upstream_keys(db_path)
    assert keys == []

    found = await find_upstream_key_by_value(db_path, "sk-example-key-1234567890abcdef")
    assert found is None or found["status"] == "revoked"


@pytest.mark.asyncio
async def test_backfill_provider_keys_mirrors_existing(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    from janus.storage.providers_db import create_provider

    await create_provider(
        db_path,
        {
            "id": "backfill-prov",
            "prefix": "backfill-prov",
            "api_type": "openai_compat",
            "base_url": "https://api.backfill.example.com/v1",
            "api_key": "sk-backfill-existing-12345",
            "models": [],
            "allowed_models": [],
        },
    )
    count = await backfill_provider_keys(db_path)
    assert count == 1

    keys = await list_upstream_keys(db_path)
    assert len(keys) == 1
    assert keys[0]["provider_id"] == "backfill-prov"

    count_again = await backfill_provider_keys(db_path)
    assert count_again == 0

import pytest

from janus.storage.routing_overview import get_routing_overview
from janus.storage.settings import require_api_key_enabled, resolve_server_settings
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key


def test_resolve_server_settings_defaults_require_api_key_on():
    resolved = resolve_server_settings({})
    assert resolved["server_require_api_key"] == "true"
    assert require_api_key_enabled({}) is True


def test_resolve_server_settings_respects_explicit_false():
    resolved = resolve_server_settings({"server_require_api_key": "false"})
    assert require_api_key_enabled(resolved) is False


@pytest.mark.asyncio
async def test_get_routing_overview_orders_inventory_keys(tmp_path) -> None:
    from janus.storage.database import init_db, seed_inventory_providers
    from janus.storage.providers_db import create_provider

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_inventory_providers(db_path)
    await create_provider(
        db_path,
        {
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": None,
            "models": ["gpt-4o"],
        },
    )
    low = await create_upstream_key(db_path, provider_id="openai", key_value="sk-low-priority")
    high = await create_upstream_key(db_path, provider_id="openai", key_value="sk-high-priority")
    await update_upstream_key(db_path, low["id"], {"status": "active", "is_valid": 1, "is_usable": 1})
    await update_upstream_key(
        db_path,
        high["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1, "priority": 50},
    )

    overview = await get_routing_overview(db_path)
    openai = next(item for item in overview["providers"] if item["prefix"] == "openai")
    assert [account["key_id"] for account in openai["accounts"]] == [high["id"], low["id"]]
    assert openai["accounts"][0]["priority"] == 50

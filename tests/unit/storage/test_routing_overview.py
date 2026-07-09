import time

import pytest

from janus.storage.cooldowns import save_cooldown
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
    await update_upstream_key(
        db_path,
        low["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1},
    )
    await update_upstream_key(
        db_path,
        high["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1, "priority": 50},
    )

    overview = await get_routing_overview(db_path)
    openai = next(item for item in overview["providers"] if item["prefix"] == "openai")
    assert [account["key_id"] for account in openai["accounts"]] == [high["id"], low["id"]]
    assert openai["accounts"][0]["priority"] == 50


@pytest.mark.asyncio
async def test_get_routing_overview_reports_per_model_cooldown(tmp_path) -> None:
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
    key = await create_upstream_key(db_path, provider_id="openai", key_value="sk-key")
    await update_upstream_key(
        db_path,
        key["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1},
    )

    await save_cooldown(
        db_path, str(key["id"]), time.time() + 100, model="gpt-4o", error_type="rate_limit"
    )

    overview = await get_routing_overview(db_path)
    openai = next(item for item in overview["providers"] if item["prefix"] == "openai")
    account = openai["accounts"][0]
    assert account["cooldown_active"] is True
    assert 0 < account["cooldown_seconds"] <= 100
    assert overview["cooldown_count"] == 1


@pytest.mark.asyncio
async def test_get_routing_overview_includes_quota_status(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.providers_db import create_provider
    from janus.storage.usage import record_usage

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "sub",
            "prefix": "sub",
            "api_type": "openai_compat",
            "base_url": "https://fake.local/v1",
            "api_key": "sk-test",
            "models": ["m1"],
            "quota_window": "daily",
            "quota_limit": 10,
            "quota_metric": "requests",
        },
    )
    for _ in range(3):
        await record_usage(db_path, provider_id="sub", model="m1")

    overview = await get_routing_overview(db_path)
    provider = overview["providers"][0]
    assert provider["quota"] is not None
    assert "status" in provider["quota"]
    assert provider["quota"]["used"] == 3
    assert provider["quota"]["limit"] == 10
    assert provider["quota"]["status"] == "ok"
    assert overview["quota_warnings"] == []


@pytest.mark.asyncio
async def test_get_routing_overview_quota_warnings_and_deprioritized(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.providers_db import create_provider
    from janus.storage.usage import record_usage

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "sub",
            "prefix": "sub",
            "api_type": "openai_compat",
            "base_url": "https://fake.local/v1",
            "api_key": "sk-test",
            "models": ["m1"],
            "quota_window": "daily",
            "quota_limit": 10,
            "quota_metric": "requests",
        },
    )
    for _ in range(8):
        await record_usage(db_path, provider_id="sub", model="m1")

    overview = await get_routing_overview(db_path)
    provider = overview["providers"][0]
    assert provider["quota"]["status"] == "warning"
    assert len(overview["quota_warnings"]) == 1
    assert overview["quota_warnings"][0]["id"] == "sub"
    assert provider["accounts"][0]["quota_deprioritized"] is False

    for _ in range(2):
        await record_usage(db_path, provider_id="sub", model="m1")

    overview = await get_routing_overview(db_path)
    provider = overview["providers"][0]
    assert provider["quota"]["status"] == "exhausted"
    assert len(overview["quota_warnings"]) == 1
    assert overview["quota_warnings"][0]["quota"]["status"] == "exhausted"
    assert provider["accounts"][0]["quota_deprioritized"] is True

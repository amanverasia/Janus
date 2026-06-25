import json

import pytest

from janus.storage.database import init_db
from janus.storage.providers_db import (
    create_provider,
    delete_provider,
    get_provider,
    list_providers,
    toggle_provider,
    update_provider,
)


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_provider(db):
    await create_provider(db, {
        "id": "openai",
        "prefix": "openai",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-xxx",
        "models": ["gpt-4o", "gpt-4o-mini"],
    })
    providers = await list_providers(db)
    assert len(providers) == 1
    assert providers[0]["id"] == "openai"
    assert providers[0]["is_enabled"] == 1
    assert json.loads(providers[0]["models"]) == ["gpt-4o", "gpt-4o-mini"]


async def test_get_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": None,
        "models": [],
    })
    p = await get_provider(db, "test")
    assert p["id"] == "test"
    assert p["api_key"] is None


async def test_get_provider_not_found(db):
    assert await get_provider(db, "nonexistent") is None


async def test_update_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://old.local",
        "api_key": "old",
        "models": ["m1"],
    })
    await update_provider(db, "test", {
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://new.local",
        "api_key": "new",
        "models": ["m1", "m2"],
    })
    p = await get_provider(db, "test")
    assert p["base_url"] == "https://new.local"
    assert json.loads(p["models"]) == ["m1", "m2"]


async def test_toggle_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": None,
        "models": [],
    })
    await toggle_provider(db, "test")
    p = await get_provider(db, "test")
    assert p["is_enabled"] == 0
    await toggle_provider(db, "test")
    p = await get_provider(db, "test")
    assert p["is_enabled"] == 1


async def test_delete_provider(db):
    await create_provider(db, {
        "id": "test",
        "prefix": "test",
        "api_type": "openai_compat",
        "base_url": "https://test.local",
        "api_key": None,
        "models": [],
    })
    await delete_provider(db, "test")
    assert await get_provider(db, "test") is None


async def test_list_providers_only_enabled(db):
    await create_provider(db, {
        "id": "a",
        "prefix": "a",
        "api_type": "openai_compat",
        "base_url": "https://a.local",
        "api_key": None,
        "models": [],
    })
    await create_provider(db, {
        "id": "b",
        "prefix": "b",
        "api_type": "openai_compat",
        "base_url": "https://b.local",
        "api_key": None,
        "models": [],
    })
    await toggle_provider(db, "b")
    enabled = await list_providers(db, enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0]["id"] == "a"
    all_p = await list_providers(db, enabled_only=False)
    assert len(all_p) == 2

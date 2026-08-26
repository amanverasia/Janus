import sqlite3

import pytest

from janus.storage.custom_models import (
    create_custom_model,
    delete_custom_model,
    get_custom_model,
    list_custom_models,
    toggle_custom_model,
    update_custom_model,
)
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider, delete_provider


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "provider-row",
            "prefix": "provider",
            "api_type": "openai_compat",
            "base_url": "https://provider.example/v1",
            "models": [],
        },
    )
    return db_path


async def test_custom_model_crud_decodes_metadata(db):
    created = await create_custom_model(
        db,
        {
            "id": "custom-row",
            "provider_id": "provider-row",
            "model_id": "model-alpha",
            "display_name": "Model Alpha",
            "context_window": 128000,
            "max_output_tokens": 16384,
            "input_modalities": ["text", "image"],
            "reasoning_efforts": ["low", "high"],
            "capabilities": {"tools": True},
        },
    )
    assert created["input_modalities"] == ["text", "image"]
    assert created["reasoning_efforts"] == ["low", "high"]
    assert created["capabilities"] == {"tools": True}
    assert created["is_enabled"] is True

    updated = await update_custom_model(
        db,
        "custom-row",
        {"display_name": "Alpha 2", "context_window": 200000},
    )
    assert updated is not None
    assert updated["display_name"] == "Alpha 2"
    assert updated["context_window"] == 200000
    assert updated["input_modalities"] == ["text", "image"]

    toggled = await toggle_custom_model(db, "custom-row")
    assert toggled is not None
    assert toggled["is_enabled"] is False
    assert await list_custom_models(db, enabled_only=True) == []
    assert await delete_custom_model(db, "custom-row") is True
    assert await get_custom_model(db, "custom-row") is None


async def test_list_custom_models_filters_provider(db):
    await create_custom_model(
        db,
        {"provider_id": "provider-row", "model_id": "model-alpha"},
    )
    assert len(await list_custom_models(db, provider_id="provider-row")) == 1
    assert await list_custom_models(db, provider_id="other") == []


async def test_deleting_provider_removes_custom_models(db):
    created = await create_custom_model(
        db,
        {"provider_id": "provider-row", "model_id": "model-alpha"},
    )
    await delete_provider(db, "provider-row")
    assert await get_custom_model(db, created["id"]) is None


async def test_custom_model_rejects_unknown_provider(db):
    with pytest.raises(sqlite3.IntegrityError, match="unknown custom model provider"):
        await create_custom_model(
            db,
            {"provider_id": "missing-provider", "model_id": "model-alpha"},
        )

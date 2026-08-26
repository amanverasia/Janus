from __future__ import annotations

import pytest

from janus.models.catalog import list_catalog_models, set_model_visibility
from janus.storage.custom_models import (
    create_custom_model,
    delete_custom_model,
    toggle_custom_model,
    update_custom_model,
)
from janus.storage.database import get_connection, init_db
from janus.storage.providers_db import create_provider, toggle_provider, update_provider
from janus.storage.upstream_models import replace_models_for_key

pytestmark = pytest.mark.asyncio


async def test_catalog_merges_configured_discovered_and_custom_models(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "openai",
            "catalog_id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-static"],
            "default_model": "gpt-static",
            "live_models": True,
        },
    )
    await replace_models_for_key(
        db_path,
        upstream_key_id="inventory-account",
        provider_id="openai",
        models=[
            {
                "model_id": "gpt-live",
                "display_name": "GPT Live",
                "context_window": 123_000,
                "max_output_tokens": 8_000,
                "capabilities": {"vision": True, "reasoning": True},
            }
        ],
    )
    custom = await create_custom_model(
        db_path,
        {
            "provider_id": "openai",
            "model_id": "gpt-custom",
            "display_name": "Custom GPT",
            "input_modalities": ["text", "image"],
            "reasoning_efforts": ["low", "high"],
            "capabilities": {"tool_use": True},
        },
    )

    rows = {row["namespaced"]: row for row in await list_catalog_models(db_path)}

    assert set(rows) == {"openai/gpt-static", "openai/gpt-live", "openai/gpt-custom"}
    assert rows["openai/gpt-static"]["source"] == "configured"
    assert rows["openai/gpt-static"]["default"] is True
    assert rows["openai/gpt-live"]["source"] == "discovered"
    assert rows["openai/gpt-live"]["context_window"] == 123_000
    assert rows["openai/gpt-live"]["max_output_tokens"] == 8_000
    assert rows["openai/gpt-live"]["input_modalities"] == ["text", "image"]
    assert rows["openai/gpt-live"]["reasoning_efforts"] == ["low", "medium", "high"]
    assert rows["openai/gpt-custom"]["source"] == "custom"
    assert rows["openai/gpt-custom"]["provider_enabled"] is True
    assert rows["openai/gpt-custom"]["custom_enabled"] is True
    assert rows["openai/gpt-custom"]["custom_id"] == custom["id"]
    assert rows["openai/gpt-custom"]["provider_id"] == "openai"


async def test_live_models_toggle_controls_inventory_discovery(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "models": ["static"],
            "live_models": False,
        },
    )
    await replace_models_for_key(
        db_path,
        upstream_key_id="inventory-account",
        provider_id="openai",
        models=[{"model_id": "live"}],
    )

    names = {row["namespaced"] for row in await list_catalog_models(db_path)}
    assert names == {"openai/static"}

    await update_provider(db_path, "openai", {"live_models": True})
    names = {row["namespaced"] for row in await list_catalog_models(db_path)}
    assert names == {"openai/static", "openai/live"}


async def test_custom_models_belong_to_logical_provider_prefix(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    for provider_id in ("account-a", "account-b"):
        await create_provider(
            db_path,
            {
                "id": provider_id,
                "catalog_id": "openai",
                "prefix": "openai",
                "api_type": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "models": [],
            },
        )
    await toggle_provider(db_path, "account-a")
    custom = await create_custom_model(
        db_path,
        {"provider_id": "account-a", "model_id": "shared-custom"},
    )

    rows = await list_catalog_models(db_path, include_disabled=False)
    assert [row["namespaced"] for row in rows] == ["openai/shared-custom"]
    assert rows[0]["provider_id"] == "account-b"
    assert rows[0]["custom_id"] == custom["id"]


async def test_catalog_reports_custom_and_shared_provider_enablement(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    for provider_id in ("account-a", "account-b"):
        await create_provider(
            db_path,
            {
                "id": provider_id,
                "catalog_id": "openai",
                "prefix": "openai",
                "api_type": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "models": [],
            },
        )
    await toggle_provider(db_path, "account-a")
    custom = await create_custom_model(
        db_path,
        {
            "provider_id": "account-a",
            "model_id": "custom-model",
            "is_enabled": False,
        },
    )

    disabled_custom = (await list_catalog_models(db_path))[0]
    assert disabled_custom["provider_enabled"] is True
    assert disabled_custom["custom_enabled"] is False
    assert disabled_custom["disabled"] is True
    assert await list_catalog_models(db_path, include_disabled=False) == []

    await toggle_custom_model(db_path, custom["id"])
    enabled_sibling = (await list_catalog_models(db_path))[0]
    assert enabled_sibling["provider_id"] == "account-b"
    assert enabled_sibling["provider_enabled"] is True
    assert enabled_sibling["custom_enabled"] is True
    assert enabled_sibling["disabled"] is False

    await toggle_provider(db_path, "account-b")
    disabled_providers = (await list_catalog_models(db_path))[0]
    assert disabled_providers["provider_enabled"] is False
    assert disabled_providers["custom_enabled"] is True
    assert disabled_providers["disabled"] is True


async def test_disabled_custom_overlap_retains_management_metadata_and_active_source(
    tmp_path,
) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "provider-row",
            "catalog_id": "custom",
            "prefix": "provider",
            "api_type": "openai_compat",
            "base_url": "https://provider.example/v1",
            "models": ["overlap"],
        },
    )
    custom = await create_custom_model(
        db_path,
        {
            "provider_id": "provider-row",
            "model_id": "overlap",
            "display_name": "Managed overlap",
            "is_enabled": False,
        },
    )

    managed = (await list_catalog_models(db_path))[0]
    assert managed["source"] == "custom"
    assert managed["custom_id"] == custom["id"]
    assert managed["custom_enabled"] is False
    assert managed["display_name"] == "Managed overlap"
    assert managed["disabled"] is False
    assert [
        row["namespaced"] for row in await list_catalog_models(db_path, include_disabled=False)
    ] == ["provider/overlap"]

    updated = await update_custom_model(
        db_path,
        custom["id"],
        {"display_name": "Edited while disabled"},
    )
    assert updated is not None
    edited = (await list_catalog_models(db_path))[0]
    assert edited["custom_id"] == custom["id"]
    assert edited["custom_enabled"] is False
    assert edited["display_name"] == "Edited while disabled"

    await toggle_custom_model(db_path, custom["id"])
    enabled = (await list_catalog_models(db_path))[0]
    assert enabled["source"] == "custom"
    assert enabled["custom_enabled"] is True
    assert enabled["disabled"] is False

    assert await delete_custom_model(db_path, custom["id"]) is True
    configured = (await list_catalog_models(db_path))[0]
    assert configured["source"] == "configured"
    assert configured["custom_id"] is None
    assert configured["custom_enabled"] is True


async def test_visibility_is_catalog_only_and_can_represent_none_selected(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await create_provider(
        db_path,
        {
            "id": "test-provider",
            "prefix": "test",
            "api_type": "openai_compat",
            "base_url": "https://provider.example/v1",
            "models": ["one", "two"],
        },
    )

    await set_model_visibility(
        db_path,
        scope="models",
        provider="test",
        targets=["one"],
        enabled=False,
    )
    visible = {row["id"] for row in await list_catalog_models(db_path, include_disabled=False)}
    assert visible == {"two"}

    await set_model_visibility(
        db_path,
        scope="models",
        provider="test-provider",
        targets=["two"],
        enabled=False,
    )
    assert await list_catalog_models(db_path, include_disabled=False) == []

    await set_model_visibility(
        db_path,
        scope="provider",
        provider="test-provider",
        targets=[],
        enabled=True,
    )
    visible = {row["id"] for row in await list_catalog_models(db_path, include_disabled=False)}
    assert visible == {"one", "two"}

    with pytest.raises(ValueError, match="Unknown model target"):
        await set_model_visibility(
            db_path,
            scope="models",
            provider="test-provider",
            targets=["missing"],
            enabled=False,
        )


async def test_visibility_updates_every_provider_instance_sharing_a_prefix(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    for provider_id in ("account-a", "account-b"):
        await create_provider(
            db_path,
            {
                "id": provider_id,
                "catalog_id": "openai",
                "prefix": "shared",
                "api_type": "openai_compat",
                "base_url": "https://provider.example/v1",
                "models": ["same-model"],
            },
        )

    await set_model_visibility(
        db_path,
        scope="models",
        provider="openai",
        targets=["same-model"],
        enabled=False,
    )

    assert await list_catalog_models(db_path, include_disabled=False) == []
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT selected_models FROM providers WHERE prefix = 'shared' ORDER BY id"
        ) as cursor:
            selections = [row["selected_models"] for row in await cursor.fetchall()]
    assert selections == [
        '["__janus_no_models_selected__"]',
        '["__janus_no_models_selected__"]',
    ]


async def test_catalog_visibility_is_unambiguous_when_row_id_matches_catalog(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    for provider_id in ("openai", "openai-secondary"):
        await create_provider(
            db_path,
            {
                "id": provider_id,
                "catalog_id": "openai",
                "prefix": "openai",
                "api_type": "openai_compat",
                "base_url": "https://api.openai.com/v1",
                "models": ["gpt-4o"],
            },
        )

    await set_model_visibility(
        db_path,
        scope="provider",
        provider="openai",
        provider_match="catalog",
        targets=[],
        enabled=False,
    )

    assert await list_catalog_models(db_path, include_disabled=False) == []


async def test_shared_prefix_is_visible_when_any_provider_row_exposes_model(tmp_path) -> None:
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    for provider_id in ("account-a", "account-b"):
        await create_provider(
            db_path,
            {
                "id": provider_id,
                "catalog_id": "shared",
                "prefix": "shared",
                "api_type": "openai_compat",
                "base_url": f"https://{provider_id}.example/v1",
                "models": ["model"],
            },
        )
    await replace_models_for_key(
        db_path,
        upstream_key_id="shared-inventory-account",
        provider_id="shared",
        models=[{"model_id": "live-model"}],
    )

    initial = await list_catalog_models(db_path, include_disabled=False)
    assert {row["namespaced"] for row in initial} == {"shared/model", "shared/live-model"}
    assert {row["provider_id"] for row in initial} == {"account-a"}

    await set_model_visibility(
        db_path,
        scope="provider",
        provider="account-a",
        targets=[],
        enabled=False,
    )
    after = await list_catalog_models(db_path, include_disabled=False)

    assert {row["namespaced"] for row in after} == {"shared/model", "shared/live-model"}
    assert {row["provider_id"] for row in after} == {"account-b"}
    assert all(row["selected"] for row in after)
    assert all(not row["disabled"] for row in after)

from __future__ import annotations

import pytest

from janus.inventory.ingestion import KeyIngestEntry
from janus.routing.provider_provision import (
    build_provision_preview,
    detect_inventory_provider_for_key,
    routing_catalog_id_for_inventory,
)


def test_routing_catalog_maps_google_to_gemini() -> None:
    assert routing_catalog_id_for_inventory("google") == "gemini"
    assert routing_catalog_id_for_inventory("dashscope") == "qwen"
    assert routing_catalog_id_for_inventory("openrouter") == "openrouter"


def test_detect_openrouter_key() -> None:
    key = "sk-or-v1-" + "a" * 20
    assert detect_inventory_provider_for_key(key) == "openrouter"


def test_preview_create_when_no_routing_provider() -> None:
    keys = ["sk-or-v1-" + "x" * 20 for _ in range(3)]
    entries = [KeyIngestEntry(key=k) for k in keys]
    preview = build_provision_preview(entries, existing_providers=[])
    assert len(preview.groups) == 1
    group = preview.groups[0]
    assert group.inventory_provider_id == "openrouter"
    assert group.key_count == 3
    assert group.action == "create"
    assert group.routing_prefix == "openrouter"


def test_preview_exists_when_routing_provider_present() -> None:
    keys = ["sk-or-v1-" + "x" * 20]
    entries = [KeyIngestEntry(key=k) for k in keys]
    existing = [
        {
            "id": "openrouter",
            "prefix": "openrouter",
            "is_enabled": 1,
        }
    ]
    preview = build_provision_preview(entries, existing_providers=existing)
    assert preview.groups[0].action == "exists"
    assert preview.needs_confirmation is False


def test_preview_enable_when_provider_disabled() -> None:
    keys = ["sk-or-v1-" + "x" * 20]
    entries = [KeyIngestEntry(key=k) for k in keys]
    existing = [{"id": "openrouter", "prefix": "openrouter", "is_enabled": 0}]
    preview = build_provision_preview(entries, existing_providers=existing)
    assert preview.groups[0].action == "enable"
    assert preview.needs_confirmation is True


def test_preview_unidentified_keys() -> None:
    entries = [KeyIngestEntry(key="not-a-real-key-value-here")]
    preview = build_provision_preview(entries, existing_providers=[])
    assert preview.unidentified_count == 1
    assert preview.groups[0].action == "skip"


@pytest.mark.asyncio
async def test_ensure_creates_routing_provider(tmp_path) -> None:
    from janus.routing.provider_provision import ensure_routing_providers
    from janus.storage.database import init_db
    from janus.storage.providers_db import get_provider

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    results = await ensure_routing_providers(db_path, {"openrouter"})
    assert results[0]["action"] == "created"
    row = await get_provider(db_path, "openrouter")
    assert row is not None
    assert row["prefix"] == "openrouter"

import json

import pytest

from janus.inventory.migrate import (
    format_inventory_verification,
    import_dashboard_export,
    verify_inventory,
)
from janus.storage.database import init_db
from janus.storage.providers_db import create_provider
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key


@pytest.mark.asyncio
async def test_verify_inventory_summary(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    record = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-verify-summary-key",
    )
    await update_upstream_key(
        db_path,
        record["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1},
    )
    await create_provider(
        db_path,
        {
            "id": "openai",
            "prefix": "openai",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-provider",
            "models": [],
        },
    )

    summary = await verify_inventory(db_path)
    assert summary["total"] == 1
    assert summary["routable"] == 1
    assert summary["by_status"]["active"] == 1
    assert summary["by_provider"]["openai"] == 1
    assert summary["provider_encryption"] == {"encrypted": 0, "plaintext": 1, "total": 1}
    text = format_inventory_verification(summary)
    assert "Total upstream keys: 1" in text
    assert "openai: 1" in text
    assert "Provider credential encryption: 0 encrypted, 1 plaintext" in text


@pytest.mark.asyncio
async def test_import_dashboard_json(tmp_path) -> None:
    from janus.inventory.migrate import import_dashboard_json, verify_inventory

    db_path = tmp_path / "test.db"
    payload = json.dumps(
        [{"key_value": "sk-proj-json-import-test-key", "provider_id": "openai"}]
    ).encode()
    count = await import_dashboard_json(db_path, payload, dry_run=False)
    assert count == 1
    summary = await verify_inventory(db_path)
    assert summary["total"] == 1


@pytest.mark.asyncio
async def test_import_dashboard_export_dry_run(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    export_path = tmp_path / "export.json"
    export_path.write_text(
        json.dumps(
            [
                {
                    "key_value": "sk-proj-import-dry-run-key",
                    "provider_id": "openai",
                    "status": "active",
                    "is_valid": True,
                }
            ]
        )
    )

    count = await import_dashboard_export(db_path, export_path, dry_run=True)
    assert count == 1
    assert not db_path.exists()


async def test_import_rejects_invalid_row_without_partial_writes(tmp_path) -> None:
    from janus.inventory.migrate import ImportRowError, import_dashboard_json
    from janus.storage.upstream_keys import count_upstream_keys

    db_path = tmp_path / "test.db"
    await init_db(db_path)
    payload = json.dumps(
        [
            {"key_value": "sk-proj-atomic-first-key-value", "provider_id": "openai"},
            {"key_value": "sk-proj-atomic-second-key-value", "priority": "not-a-number"},
        ]
    ).encode()

    with pytest.raises(ImportRowError, match="Row 2"):
        await import_dashboard_json(db_path, payload, dry_run=False)
    assert await count_upstream_keys(db_path) == 0


async def test_import_rolls_back_when_insert_fails_mid_file(tmp_path, monkeypatch) -> None:
    import sqlite3

    from janus.inventory.migrate import import_dashboard_json
    from janus.storage import upstream_keys
    from janus.storage.upstream_keys import count_upstream_keys

    db_path = tmp_path / "test.db"
    await init_db(db_path)
    monkeypatch.setattr(upstream_keys, "_new_key_id", lambda: "fixed-id")
    payload = json.dumps(
        [
            {"key_value": "sk-proj-rollback-first-key-value", "provider_id": "openai"},
            {"key_value": "sk-proj-rollback-second-key-value", "provider_id": "openai"},
        ]
    ).encode()

    with pytest.raises(sqlite3.IntegrityError):
        await import_dashboard_json(db_path, payload, dry_run=False)
    assert await count_upstream_keys(db_path) == 0


async def test_reimport_skips_existing_and_in_file_duplicates(tmp_path) -> None:
    from janus.inventory.migrate import import_dashboard_json_with_ids
    from janus.storage.upstream_keys import count_upstream_keys

    db_path = tmp_path / "test.db"
    payload = json.dumps(
        [
            {"key_value": "sk-proj-dedupe-first-key-value", "provider_id": "openai"},
            {"key_value": "sk-proj-dedupe-first-key-value", "provider_id": "openai"},
            {"key_value": "sk-proj-dedupe-second-key-value", "provider_id": "openai"},
            {"provider_id": "openai"},
        ]
    ).encode()

    first = await import_dashboard_json_with_ids(db_path, payload, dry_run=False)
    assert (first.imported, first.duplicates, first.skipped) == (2, 1, 1)
    assert len(first.imported_ids) == 2

    second = await import_dashboard_json_with_ids(db_path, payload, dry_run=False)
    assert (second.imported, second.duplicates, second.skipped) == (0, 3, 1)
    assert second.imported_ids == []
    assert await count_upstream_keys(db_path) == 2


async def test_import_preserves_exported_state_unless_reset(tmp_path) -> None:
    from janus.inventory.migrate import import_dashboard_json_with_ids
    from janus.storage.upstream_keys import get_upstream_key

    db_path = tmp_path / "test.db"
    row = {
        "key_value": "sk-proj-state-preserve-key-value",
        "provider_id": "openai",
        "status": "active",
        "is_valid": True,
        "is_usable": True,
        "credits_remaining": 12.5,
        "last_error": "old",
        "key_label": "work",
        "priority": 3,
    }
    kept = await import_dashboard_json_with_ids(db_path, json.dumps([row]).encode(), dry_run=False)
    stored = await get_upstream_key(db_path, kept.imported_ids[0])
    assert stored is not None
    assert stored["status"] == "active"
    assert (stored["is_valid"], stored["is_usable"]) == (1, 1)
    assert stored["credits_remaining"] == 12.5
    assert stored["key_label"] == "work"
    assert stored["priority"] == 3
    assert stored["health_status"] == "healthy"

    row["key_value"] = "sk-proj-state-reset-key-value"
    reset = await import_dashboard_json_with_ids(
        db_path, json.dumps([row]).encode(), dry_run=False, reset_validation=True
    )
    stored = await get_upstream_key(db_path, reset.imported_ids[0])
    assert stored is not None
    assert stored["status"] == "pending_validation"
    assert (stored["is_valid"], stored["is_usable"]) == (0, 0)
    assert stored["last_error"] is None

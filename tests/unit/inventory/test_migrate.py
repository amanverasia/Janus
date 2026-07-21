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

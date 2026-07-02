import json

import pytest

from janus.inventory.migrate import import_dashboard_export


@pytest.mark.asyncio
async def test_import_dashboard_export_dry_run(tmp_path):
    export_path = tmp_path / "export.json"
    db_path = tmp_path / "janus.db"
    export_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_value": "sk-proj-import-test",
                        "provider_id": "openai",
                        "status": "active",
                        "is_valid": 1,
                        "credits_remaining": 10.0,
                    }
                ]
            }
        )
    )
    count = await import_dashboard_export(db_path, export_path, dry_run=True)
    assert count == 1
    assert not db_path.exists()


@pytest.mark.asyncio
async def test_import_dashboard_export_writes_rows(tmp_path):
    export_path = tmp_path / "export.json"
    db_path = tmp_path / "janus.db"
    export_path.write_text(
        json.dumps(
            [
                {
                    "key_value": "gsk_import_test",
                    "provider_id": "groq",
                    "status": "pending_validation",
                }
            ]
        )
    )
    count = await import_dashboard_export(db_path, export_path, dry_run=False)
    assert count == 1
    assert db_path.exists()

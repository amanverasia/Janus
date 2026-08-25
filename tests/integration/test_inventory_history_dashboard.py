import re

import pytest
from httpx import ASGITransport, AsyncClient

from janus.app import create_app
from janus.config.schema import JanusConfig, ServerSettings
from janus.inventory.ingestion import KeyIngestEntry, ingest_upstream_key
from janus.storage.database import init_db, seed_from_config
from janus.storage.upstream_keys import record_upstream_key_history


@pytest.mark.asyncio
async def test_inventory_history_renders_unit_neutral_balance_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_SCHEDULER_ENABLED", "false")
    app = create_app(config=JanusConfig(server=ServerSettings(port=0, data_dir=tmp_path)))
    await init_db(app.state.db_path)
    await seed_from_config(app.state.db_path, app.state.config)
    key = await ingest_upstream_key(
        app.state.db_path,
        KeyIngestEntry(key="sk-proj-history-dashboard"),
        chosen_provider="openai",
    )
    await record_upstream_key_history(
        app.state.db_path,
        upstream_key_id=key["id"],
        previous_status="pending_validation",
        new_status="active",
        credits_remaining=12.5,
    )
    await record_upstream_key_history(
        app.state.db_path,
        upstream_key_id=key["id"],
        previous_status="active",
        new_status="invalid",
        credits_remaining=None,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        overview = await client.get("/dashboard/inventory")
        detail = await client.get(f"/dashboard/api/inventory/keys/{key['id']}/partial")

    assert overview.status_code == 200
    assert detail.status_code == 200
    for body in (overview.text, detail.text):
        assert "Balance snapshot" in body
        assert "12.50 credits" in body
        assert "$12.50" not in body
        assert re.search(
            r"data-history-balance[^>]*>(?:\s*—|.*?Balance snapshot:\s*—)",
            body,
            re.DOTALL,
        )

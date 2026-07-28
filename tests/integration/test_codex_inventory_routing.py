import json

import pytest

from janus.inventory.ingestion import KeyIngestEntry, ingest_upstream_key
from janus.routing.provider_provision import ensure_routing_providers
from janus.routing.upstream_expand import expand_gateway_provider
from janus.storage.database import init_db, seed_inventory_providers
from janus.storage.providers_db import list_providers
from janus.storage.upstream_keys import list_routable_upstream_keys, update_upstream_key


@pytest.mark.asyncio
async def test_codex_inventory_expands_multi_account(tmp_path) -> None:
    db = tmp_path / "janus.db"
    await init_db(db)
    await seed_inventory_providers(db)

    for i, wid in enumerate(("w1", "w2"), start=1):
        blob = json.dumps(
            {
                "access_token": f"at-{i}-long-enough",
                "refresh_token": f"rt-{i}-long-enough",
                "extra": {"workspaceId": wid},
            }
        )
        result = await ingest_upstream_key(
            db, KeyIngestEntry(key=blob, label=f"c{i}"), chosen_provider="codex"
        )
        assert result["status"] == "registered"
        await update_upstream_key(
            db,
            result["id"],
            {
                "status": "active",
                "is_valid": 1,
                "is_usable": 1,
            },
        )

    await ensure_routing_providers(db, {"codex"})
    providers = await list_providers(db)
    codex_rows = [p for p in providers if p["prefix"] == "codex" or p["id"] == "codex"]
    assert codex_rows
    row = codex_rows[0]
    keys = await list_routable_upstream_keys(db, "codex")
    assert len(keys) == 2
    configs = expand_gateway_provider(row, keys)
    assert len(configs) == 2
    assert all(c.api_type == "codex" for c in configs)
    assert all("access_token" in (c.api_key or "") for c in configs)

from __future__ import annotations

import httpx
import pytest
import respx

from janus.inventory.ingestion import KeyIngestEntry, ingest_upstream_key
from janus.inventory.key_checker import validate_key
from janus.inventory.provider_detection import resolve_provider_for_key
from janus.inventory.xiaomi_tokenplan import (
    TOKENPLAN_PROVIDER_ID,
    TOKENPLAN_REGIONS,
    tokenplan_region_base_urls,
)
from janus.storage.database import init_db
from janus.storage.upstream_keys import get_upstream_key


def test_tokenplan_region_order() -> None:
    regions = [r for r, _ in tokenplan_region_base_urls()]
    assert regions == ["sgp", "cn", "ams"]


@pytest.mark.asyncio
@respx.mock
async def test_validate_tokenplan_tries_regions_until_auth() -> None:
    sgp = TOKENPLAN_REGIONS["sgp"] + "/models"
    cn = TOKENPLAN_REGIONS["cn"] + "/models"
    respx.get(sgp).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    respx.get(cn).mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "mimo-v2.5-pro", "object": "model"}]}
        )
    )
    respx.get(TOKENPLAN_REGIONS["ams"] + "/models").mock(
        return_value=httpx.Response(401, json={"error": "nope"})
    )
    result = await validate_key("tp-cn-key-xxxxxxxx", TOKENPLAN_PROVIDER_ID, skip_probe=True)
    assert result["is_valid"] is True
    assert result["custom_base_url"] == TOKENPLAN_REGIONS["cn"]
    assert result["tokenplan_region"] == "cn"
    assert result["metadata"]["tokenplan_region"] == "cn"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_tp_key_never_returns_paygo_xiaomi() -> None:
    for base in TOKENPLAN_REGIONS.values():
        respx.get(base + "/models").mock(return_value=httpx.Response(401, json={}))
    # even if paygo would accept (should not be tried)
    respx.get("https://api.xiaomimimo.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    provider_id, meta = await resolve_provider_for_key("tp-dead-key-xxxxxxxx")
    assert provider_id == TOKENPLAN_PROVIDER_ID
    assert meta is None or meta.get("custom_base_url") is None


@pytest.mark.asyncio
@respx.mock
async def test_resolve_tp_key_returns_region_metadata() -> None:
    respx.get(TOKENPLAN_REGIONS["sgp"] + "/models").mock(
        return_value=httpx.Response(401, json={})
    )
    respx.get(TOKENPLAN_REGIONS["cn"] + "/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "mimo-v2.5"}]})
    )
    respx.get(TOKENPLAN_REGIONS["ams"] + "/models").mock(
        return_value=httpx.Response(401, json={})
    )
    provider_id, meta = await resolve_provider_for_key("tp-cn-working-keyxxxx")
    assert provider_id == TOKENPLAN_PROVIDER_ID
    assert meta is not None
    assert meta["custom_base_url"] == TOKENPLAN_REGIONS["cn"]
    assert meta["tokenplan_region"] == "cn"


@pytest.mark.asyncio
@respx.mock
async def test_ingest_persists_tokenplan_region(tmp_path) -> None:
    db = tmp_path / "t.db"
    await init_db(db)
    respx.get(TOKENPLAN_REGIONS["sgp"] + "/models").mock(
        return_value=httpx.Response(401, json={})
    )
    respx.get(TOKENPLAN_REGIONS["cn"] + "/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "mimo-v2.5"}]})
    )
    respx.get(TOKENPLAN_REGIONS["ams"] + "/models").mock(
        return_value=httpx.Response(401, json={})
    )
    result = await ingest_upstream_key(
        db,
        KeyIngestEntry(key="tp-cn-working-keyxxxx"),
        chosen_provider="auto",
    )
    assert result["status"] == "registered"
    assert result["provider_id"] == TOKENPLAN_PROVIDER_ID
    row = await get_upstream_key(db, result["id"])
    assert row is not None
    assert row["provider_id"] == TOKENPLAN_PROVIDER_ID
    assert row["custom_base_url"] == TOKENPLAN_REGIONS["cn"]

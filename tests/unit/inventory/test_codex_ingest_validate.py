import json

import httpx
import pytest
import respx

from janus.dashboard.inventory_routes import _parse_bulk_keys
from janus.inventory.ingestion import KeyIngestEntry, ingest_upstream_key, validate_key_value
from janus.inventory.key_checker import CODEX_RESPONSES_URL, validate_key
from janus.providers.oauth_tokens import CODEX_TOKEN_URL
from janus.storage.database import init_db, seed_inventory_providers
from janus.storage.upstream_keys import get_upstream_key


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_inventory_providers(db_path)
    return db_path


def test_validate_allows_long_codex_json() -> None:
    blob = json.dumps({"access_token": "x" * 2000, "refresh_token": "y" * 200})
    assert len(blob) > 512
    assert validate_key_value(blob, provider_id="codex") is None


def test_validate_still_rejects_short_garbage_for_normal_keys() -> None:
    assert validate_key_value("short") is not None


@pytest.mark.asyncio
async def test_ingest_preserves_multiline_json_via_normalize(db) -> None:
    raw = (
        '{\n  "accessToken": "at-long-enough-value",\n'
        '  "refreshToken": "rt-long-enough-value",\n'
        '  "providerSpecificData": {"chatgptAccountId": "w"}\n}'
    )
    result = await ingest_upstream_key(
        db,
        KeyIngestEntry(key=raw, label="acct"),
        chosen_provider="codex",
    )
    assert result["status"] == "registered"
    row = await get_upstream_key(db, result["id"])
    assert row is not None
    stored = row["key_value"]
    assert "\n" not in stored
    assert json.loads(stored)["extra"]["workspaceId"] == "w"


def test_parse_bulk_keys_provider_connections() -> None:
    raw = json.dumps(
        {
            "providerConnections": [
                {
                    "provider": "codex",
                    "accessToken": "atok-long",
                    "refreshToken": "rtok-long",
                    "name": "n1",
                },
                {"provider": "nvidia", "accessToken": "nv"},
            ]
        }
    )
    entries = _parse_bulk_keys(raw)
    assert len(entries) == 1
    assert entries[0]["label"] == "n1"
    assert "access_token" in entries[0]["key"]


@pytest.mark.asyncio
@respx.mock
async def test_codex_validate_refresh_success() -> None:
    route = respx.post(CODEX_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expires_in": 3600,
            },
        )
    )
    blob = json.dumps(
        {
            "access_token": "old-at",
            "refresh_token": "old-rt",
            "extra": {"workspaceId": "w"},
        }
    )
    result = await validate_key(blob, "codex")
    assert result["is_valid"] is True
    assert route.called
    assert "key_value" in result
    stored = json.loads(result["key_value"])
    assert stored["access_token"] == "new-at"
    assert stored["extra"]["workspaceId"] == "w"


@pytest.mark.asyncio
@respx.mock
async def test_codex_validate_refresh_failure_falls_back_to_access_token() -> None:
    respx.post(CODEX_TOKEN_URL).mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {
                    "code": "refresh_token_reused",
                    "message": "refresh token already used",
                }
            },
        )
    )
    respx.post(CODEX_RESPONSES_URL).mock(
        return_value=httpx.Response(200, text="event: response.created\\ndata: {}\\n")
    )
    blob = json.dumps({"access_token": "old-at", "refresh_token": "old-rt"})
    result = await validate_key(blob, "codex")
    assert result["is_valid"] is True
    assert result["is_usable"] is True
    assert result["usability_status"] == "access_token_only"
    assert "already used" in result["usability_note"]


@pytest.mark.asyncio
@respx.mock
async def test_codex_validate_refresh_and_access_probe_failure() -> None:
    respx.post(CODEX_TOKEN_URL).mock(return_value=httpx.Response(401, json={}))
    respx.post(CODEX_RESPONSES_URL).mock(return_value=httpx.Response(401, json={}))
    blob = json.dumps({"access_token": "old-at", "refresh_token": "old-rt"})
    result = await validate_key(blob, "codex")
    assert result["is_valid"] is False
    assert "access-token probe failed" in result["error"]


@pytest.mark.asyncio
@respx.mock
async def test_codex_validate_probe_rate_limit_is_inconclusive() -> None:
    respx.post(CODEX_TOKEN_URL).mock(return_value=httpx.Response(401, json={}))
    respx.post(CODEX_RESPONSES_URL).mock(return_value=httpx.Response(429, json={}))
    blob = json.dumps({"access_token": "old-at", "refresh_token": "old-rt"})
    result = await validate_key(blob, "codex")
    assert result.get("is_valid") is not True
    assert result["probe_inconclusive"] is True
    assert "HTTP 429" in result["error"]

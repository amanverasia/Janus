import time

import httpx
import pytest
import respx

from janus.providers.oauth_tokens import (
    CODEX_TOKEN_URL,
    access_token,
    apply_token_response,
    needs_refresh,
    parse_credential,
    refresh_codex,
    serialize_credential,
)


def test_parse_bare_token() -> None:
    cred = parse_credential("sk-live-abc")
    assert access_token(cred) == "sk-live-abc"


def test_parse_json_blob_roundtrip() -> None:
    raw = serialize_credential({"access_token": "a", "refresh_token": "r", "expires_at": 1.0})
    cred = parse_credential(raw)
    assert access_token(cred) == "a"
    assert cred["refresh_token"] == "r"


def test_needs_refresh_by_expiry() -> None:
    assert needs_refresh({"access_token": "a", "expires_at": time.time() - 10})
    assert not needs_refresh({"access_token": "a", "expires_at": time.time() + 3600})


def test_apply_token_response() -> None:
    cred = apply_token_response(
        {"refresh_token": "old"},
        {"access_token": "new", "refresh_token": "nr", "expires_in": 3600},
    )
    assert cred["access_token"] == "new"
    assert cred["refresh_token"] == "nr"
    assert cred["expires_at"] > time.time()


@pytest.mark.asyncio
@respx.mock
async def test_refresh_codex() -> None:
    respx.post(CODEX_TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 60}
        )
    )
    async with httpx.AsyncClient() as client:
        data = await refresh_codex("old-rt", client)
    assert data is not None
    assert data["access_token"] == "at"

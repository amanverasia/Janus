from __future__ import annotations

import json
import time

import respx
from httpx import Response

from janus.inventory.key_checker import (
    ANTIGRAVITY_LOAD_CODE_ASSIST_URL,
    CODEX_RESPONSES_URL,
    _validate_antigravity_key,
    _validate_codex_key,
)
from janus.providers.oauth_tokens import CODEX_TOKEN_URL, GOOGLE_TOKEN_URL


def _codex(expires_at: float) -> str:
    return json.dumps(
        {"access_token": "at-live", "refresh_token": "rt-live", "expires_at": expires_at}
    )


def _antigravity(expires_at: float) -> str:
    return json.dumps(
        {"access_token": "at-live", "refresh_token": "rt-live", "expires_at": expires_at}
    )


@respx.mock
async def test_codex_validator_skips_refresh_when_access_token_valid() -> None:
    refresh = respx.post(CODEX_TOKEN_URL).mock(return_value=Response(200, json={}))
    respx.post(CODEX_RESPONSES_URL).mock(return_value=Response(200, text="data: {}\n\n"))
    result = await _validate_codex_key(_codex(time.time() + 3600))
    assert result["is_valid"] is True
    assert not refresh.called
    assert json.loads(result["key_value"])["refresh_token"] == "rt-live"


@respx.mock
async def test_codex_validator_refreshes_expired_token() -> None:
    refresh = respx.post(CODEX_TOKEN_URL).mock(
        return_value=Response(
            200,
            json={"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600},
        )
    )
    result = await _validate_codex_key(_codex(time.time() - 10))
    assert refresh.called
    assert json.loads(result["key_value"])["access_token"] == "at-new"


@respx.mock
async def test_codex_validator_inconclusive_probe_does_not_refresh() -> None:
    refresh = respx.post(CODEX_TOKEN_URL).mock(return_value=Response(200, json={}))
    respx.post(CODEX_RESPONSES_URL).mock(return_value=Response(503))
    result = await _validate_codex_key(_codex(time.time() + 3600))
    assert result.get("probe_inconclusive") is True
    assert not refresh.called


@respx.mock
async def test_antigravity_validator_skips_refresh_when_access_token_valid() -> None:
    refresh = respx.post(GOOGLE_TOKEN_URL).mock(return_value=Response(200, json={}))
    probe = respx.post(ANTIGRAVITY_LOAD_CODE_ASSIST_URL).mock(
        return_value=Response(401, json={"error": "unauthorized"})
    )
    await _validate_antigravity_key(_antigravity(time.time() + 3600))
    assert probe.called
    assert not refresh.called


@respx.mock
async def test_antigravity_validator_refreshes_expired_token() -> None:
    refresh = respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at-new", "expires_in": 3600})
    )
    respx.post(ANTIGRAVITY_LOAD_CODE_ASSIST_URL).mock(
        return_value=Response(401, json={"error": "unauthorized"})
    )
    await _validate_antigravity_key(_antigravity(time.time() - 10))
    assert refresh.called

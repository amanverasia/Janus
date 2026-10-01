from __future__ import annotations

import json
import time

import httpx
import respx
from httpx import Response

from janus.inventory.key_checker import validate_key
from janus.providers.oauth_tokens import CLAUDE_TOKEN_URL, CLAUDE_USAGE_URL


def _cred(expires_at: float) -> str:
    return json.dumps(
        {
            "access_token": "sk-ant-oat01-live",
            "refresh_token": "sk-ant-ort01-live",
            "expires_at": expires_at,
        }
    )


@respx.mock
async def test_valid_token_is_usable_without_refresh() -> None:
    refresh = respx.post(CLAUDE_TOKEN_URL).mock(return_value=Response(200, json={}))
    usage = respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(200, json={"five_hour": {}}))
    result = await validate_key(_cred(time.time() + 3600), "claude_oauth")
    assert result["is_valid"] is True and result["is_usable"] is True
    assert not refresh.called
    assert usage.calls.last.request.headers["anthropic-beta"] == "oauth-2025-04-20"


@respx.mock
async def test_expired_token_refreshes_once_and_returns_blob() -> None:
    refresh = respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(
            200,
            json={
                "access_token": "sk-ant-oat01-new",
                "refresh_token": "sk-ant-ort01-new",
                "expires_in": 28800,
            },
        )
    )
    respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(200, json={}))
    result = await validate_key(_cred(time.time() - 10), "claude_oauth")
    assert json.loads(result["key_value"])["access_token"] == "sk-ant-oat01-new"
    assert refresh.call_count == 1


@respx.mock
async def test_rejected_token_is_invalid() -> None:
    respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(401))
    result = await validate_key(_cred(time.time() + 3600), "claude_oauth")
    assert result["is_valid"] is False
    assert "sk-ant" not in json.dumps(result.get("error"))


@respx.mock
async def test_rate_limit_and_network_are_inconclusive() -> None:
    route = respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(429))
    assert (await validate_key(_cred(time.time() + 3600), "claude_oauth"))["probe_inconclusive"]
    route.side_effect = httpx.ConnectError("down")
    assert (await validate_key(_cred(time.time() + 3600), "claude_oauth"))["probe_inconclusive"]


@respx.mock
async def test_failed_refresh_is_invalid() -> None:
    respx.post(CLAUDE_TOKEN_URL).mock(return_value=Response(400, json={"error": "invalid_grant"}))
    result = await validate_key(_cred(time.time() - 10), "claude_oauth")
    assert result["is_valid"] is False
    assert "sk-ant" not in json.dumps(result.get("error"))


@respx.mock
async def test_inconclusive_probe_after_refresh_keeps_rotated_blob() -> None:
    respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(
            200,
            json={
                "access_token": "sk-ant-oat01-new",
                "refresh_token": "sk-ant-ort01-new",
                "expires_in": 28800,
            },
        )
    )
    respx.get(CLAUDE_USAGE_URL).mock(return_value=Response(429))
    result = await validate_key(_cred(time.time() - 10), "claude_oauth")
    assert result["probe_inconclusive"]
    assert json.loads(result["key_value"])["access_token"] == "sk-ant-oat01-new"

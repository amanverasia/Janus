from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
import respx
from httpx import Response

from janus.providers.claude_oauth import ClaudeOAuthProvider
from janus.providers.codex import CodexProvider
from janus.providers.credential_persistence import _PENDING_SAVES, credential_expiry
from janus.providers.oauth_tokens import CLAUDE_TOKEN_URL, CODEX_TOKEN_URL


class FakeStore:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.saves: list[tuple[str, str]] = []

    async def load(self) -> str | None:
        return self.value

    async def save(self, previous: str, current: str) -> bool:
        self.saves.append((previous, current))
        if previous != self.value:
            return False
        self.value = current
        return True


def _expired(**extra: Any) -> str:
    return json.dumps(
        {
            "access_token": "at-old",
            "refresh_token": "rt-old",
            "expires_at": time.time() - 10,
            **extra,
        }
    )


async def _drain() -> None:
    while _PENDING_SAVES:
        await asyncio.gather(*list(_PENDING_SAVES))


def test_credential_expiry_normalizes_milliseconds() -> None:
    assert credential_expiry({"expiresAt": 1_790_000_000_000}) == pytest.approx(1_790_000_000.0)
    assert credential_expiry({"expires_at": "bad"}) is None
    assert credential_expiry({}) is None


@respx.mock
async def test_refresh_writes_back_once() -> None:
    blob = _expired()
    respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(
            200,
            json={"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 28800},
        )
    )
    provider = ClaudeOAuthProvider(api_key=blob)
    store = FakeStore(blob)
    provider.attach_credential_store(store)
    assert await provider._ensure_token() is None
    await _drain()
    assert len(store.saves) == 1
    assert store.saves[0][0] == blob
    assert json.loads(store.value or "{}")["refresh_token"] == "rt-new"
    await provider.close()


@respx.mock
async def test_writeback_uses_exact_stored_string() -> None:
    blob = '{ "access_token" : "at-old", "refresh_token" : "rt-old", "expires_at" : 1 }'
    respx.post(CODEX_TOKEN_URL).mock(
        return_value=Response(
            200,
            json={"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600},
        )
    )
    provider = CodexProvider(api_key=blob)
    store = FakeStore(blob)
    provider.attach_credential_store(store)
    assert await provider._ensure_token() is None
    await _drain()
    assert store.saves and store.saves[0][0] == blob
    assert json.loads(store.value or "{}")["access_token"] == "at-new"
    await provider.close()


async def test_adopts_newer_stored_credential_without_refreshing() -> None:
    provider = ClaudeOAuthProvider(api_key=_expired())
    fresh = json.dumps(
        {"access_token": "at-fresh", "refresh_token": "rt-fresh", "expires_at": time.time() + 3600}
    )
    store = FakeStore(fresh)
    provider.attach_credential_store(store)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(CLAUDE_TOKEN_URL)
        assert await provider._ensure_token() is None
        assert not route.called
    assert "at-fresh" in provider.credential_blob()
    assert store.saves == []
    await provider.close()


@respx.mock
async def test_rejected_refresh_adopts_rotated_stored_credential() -> None:
    respx.post(CODEX_TOKEN_URL).mock(return_value=Response(400, json={"error": "invalid_grant"}))
    provider = CodexProvider(api_key=_expired())
    rotated = json.dumps({"access_token": "at-rot", "refresh_token": "rt-rot"})
    store = FakeStore(rotated)
    provider.attach_credential_store(store)
    result = await provider._ensure_token()
    assert result is None
    assert "at-rot" in provider.credential_blob()
    assert store.saves == []
    await provider.close()


@respx.mock
async def test_no_store_means_no_writeback_and_failure_is_not_fatal() -> None:
    respx.post(CLAUDE_TOKEN_URL).mock(
        return_value=Response(200, json={"access_token": "at-new", "expires_in": 60})
    )
    provider = ClaudeOAuthProvider(api_key=_expired())
    assert await provider._ensure_token() is None

    class BrokenStore(FakeStore):
        async def save(self, previous: str, current: str) -> bool:
            raise RuntimeError("db down")

    provider2 = ClaudeOAuthProvider(api_key=_expired())
    provider2.attach_credential_store(BrokenStore(None))
    assert await provider2._ensure_token() is None
    await _drain()
    await provider.close()
    await provider2.close()

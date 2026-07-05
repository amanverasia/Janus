from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)

GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
DEFAULT_API_BASE = "https://api.githubcopilot.com"

_EDITOR_HEADERS: dict[str, str] = {
    "Editor-Version": "vscode/1.99.0",
    "Editor-Plugin-Version": "copilot-chat/0.26.0",
    "Copilot-Integration-Id": "vscode-chat",
    "User-Agent": "GitHubCopilotChat/0.26.0",
}

_TOKEN_REFRESH_MARGIN_S = 60.0


async def start_device_flow(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Begin the GitHub device-code flow. Returns device_code, user_code, etc."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        r = await client.post(
            DEVICE_CODE_URL,
            data={"client_id": GITHUB_CLIENT_ID, "scope": "read:user"},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
            "interval": int(data.get("interval", 5)),
            "expires_in": int(data.get("expires_in", 900)),
        }
    finally:
        if owns_client:
            await client.aclose()


async def poll_device_flow(
    device_code: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Poll once for the device-flow result.

    Returns {"status": "pending"}, {"status": "success", "access_token": ...},
    or {"status": "error", "error": ...}.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        r = await client.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data: dict[str, Any] = r.json()
        if data.get("access_token"):
            return {"status": "success", "access_token": data["access_token"]}
        error = data.get("error", "unknown_error")
        if error in ("authorization_pending", "slow_down"):
            return {"status": "pending", "error": error}
        return {"status": "error", "error": error}
    finally:
        if owns_client:
            await client.aclose()


class GitHubCopilotProvider:
    """Executor for GitHub Copilot subscriptions.

    Holds the long-lived GitHub OAuth token (from the device-code flow) and
    exchanges it for short-lived Copilot session tokens, refreshed before
    expiry behind a single-flight lock. The chat endpoint is OpenAI-compatible.
    """

    name = "github_copilot"

    def __init__(self, oauth_token: str, base_url: str = DEFAULT_API_BASE) -> None:
        self.base_url = (base_url or DEFAULT_API_BASE).rstrip("/")
        self._oauth_token = oauth_token
        self._session_token: str | None = None
        self._session_expires_at: float = 0.0
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)

    def _session_valid(self) -> bool:
        return (
            self._session_token is not None
            and time.time() < self._session_expires_at - _TOKEN_REFRESH_MARGIN_S
        )

    async def _ensure_session_token(self) -> RawResult | None:
        """Refresh the Copilot session token if needed.

        Returns a RawResult on failure (so the caller can surface/classify it),
        or None on success.
        """
        if self._session_valid():
            return None
        async with self._refresh_lock:
            if self._session_valid():
                return None
            r = await self._client.get(
                COPILOT_TOKEN_URL,
                headers={
                    "Authorization": f"token {self._oauth_token}",
                    "Accept": "application/json",
                    **_EDITOR_HEADERS,
                },
            )
            if r.status_code != 200:
                detail: dict[str, Any]
                try:
                    detail = r.json()
                except ValueError:
                    detail = {"error": r.text[:500]}
                return RawResult(status_code=r.status_code, json_data=detail)
            data = r.json()
            token = data.get("token")
            if not token:
                return RawResult(
                    status_code=502,
                    json_data={"error": "Copilot token exchange returned no token"},
                )
            self._session_token = token
            expires_at = data.get("expires_at")
            if isinstance(expires_at, int | float):
                self._session_expires_at = float(expires_at)
            else:
                self._session_expires_at = time.time() + 25 * 60
            return None

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._session_token}",
            "Openai-Intent": "conversation-panel",
            **_EDITOR_HEADERS,
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        failure = await self._ensure_session_token()
        if failure is not None:
            return failure
        url = f"{self.base_url}/chat/completions"
        if stream:
            return await self._call_stream(url, payload)
        r = await self._client.post(url, json=payload, headers=self._headers())
        try:
            json_data = r.json()
        except ValueError:
            json_data = {"error": r.text[:500]}
        return RawResult(status_code=r.status_code, json_data=json_data)

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        payload = {**payload, "stream": True}

        async def line_iter() -> AsyncIterator[str]:
            async with self._client.stream("POST", url, json=payload, headers=self._headers()) as r:
                async for raw_line in r.aiter_lines():
                    yield raw_line

        return RawResult(status_code=200, lines=line_iter())

    async def list_models(self) -> list[str]:
        failure = await self._ensure_session_token()
        if failure is not None:
            return []
        r = await self._client.get(f"{self.base_url}/models", headers=self._headers())
        if r.status_code != 200:
            return []
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]

    async def close(self) -> None:
        await self._client.aclose()

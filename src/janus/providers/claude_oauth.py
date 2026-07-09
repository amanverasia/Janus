"""Claude Code subscription (Anthropic OAuth) executor.

Uses Messages API with Bearer OAuth token + Claude CLI beta headers.
Ported from 9router ``providers/registry/claude.js`` transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult, parse_error_body, parse_retry_after
from .oauth_tokens import (
    access_token,
    apply_token_response,
    needs_refresh,
    parse_credential,
    refresh_claude,
    refresh_token,
    serialize_credential,
)

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
DEFAULT_BASE = "https://api.anthropic.com"

_BETA = (
    "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
    "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
    "advanced-tool-use-2025-11-20,effort-2025-11-24,structured-outputs-2025-12-15,"
    "fast-mode-2026-02-01,redact-thinking-2026-02-12,token-efficient-tools-2026-03-28"
)


class ClaudeOAuthProvider:
    name = "claude_oauth"

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE) -> None:
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self._cred = parse_credential(api_key)
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)

    def credential_blob(self) -> str:
        return serialize_credential(self._cred)

    async def _ensure_token(self) -> RawResult | None:
        if not needs_refresh(self._cred):
            return None
        rt = refresh_token(self._cred)
        if not rt:
            return None
        async with self._refresh_lock:
            if not needs_refresh(self._cred):
                return None
            tokens = await refresh_claude(rt, self._client)
            if tokens is None:
                return RawResult(
                    status_code=401,
                    json_data={"error": "Claude OAuth refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token(self._cred)}",
            "Anthropic-Version": "2023-06-01",
            "Anthropic-Beta": _BETA,
            "Anthropic-Dangerous-Direct-Browser-Access": "true",
            "User-Agent": "claude-cli/2.1.92 (external, sdk-cli)",
            "X-App": "cli",
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        err = await self._ensure_token()
        if err is not None:
            return err
        url = f"{self.base_url}/v1/messages?beta=true"
        body = dict(payload)
        if stream:
            body["stream"] = True
            return await self._call_stream(url, body)
        r = await self._client.post(url, json=body, headers=self._headers())
        if r.status_code >= 400:
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(r.content),
                retry_after=parse_retry_after(r.headers),
            )
        try:
            data = r.json()
        except Exception:
            data = {"error": r.text[:500]}
        return RawResult(status_code=r.status_code, json_data=data)

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        cm = self._client.stream("POST", url, json=payload, headers=self._headers())
        r = await cm.__aenter__()
        if r.status_code >= 400:
            body = await r.aread()
            await cm.__aexit__(None, None, None)
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(body),
                retry_after=parse_retry_after(r.headers),
            )

        async def line_iter() -> AsyncIterator[str]:
            try:
                async for raw_line in r.aiter_lines():
                    yield raw_line
            finally:
                await cm.__aexit__(None, None, None)

        return RawResult(status_code=r.status_code, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()

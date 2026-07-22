"""Claude Code subscription (Anthropic OAuth) executor.

Uses Messages API with Bearer OAuth token + Claude CLI beta headers.
Ported from 9router ``providers/registry/claude.js`` transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from janus.routing.claude_beta import apply_claude_upstream_headers

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

    def _headers(
        self,
        *,
        stream: bool = False,
        client_headers: dict[str, str] | None = None,
        extra_betas: list[str] | None = None,
    ) -> dict[str, str]:
        base = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token(self._cred)}",
        }
        return apply_claude_upstream_headers(
            base,
            incoming_headers=client_headers,
            extra_betas=extra_betas,
            oauth=True,
            stream=stream,
            session_seed=access_token(self._cred),
            api_key_auth=False,
        )

    async def call(
        self,
        payload: dict[str, Any],
        stream: bool = False,
        *,
        client_headers: dict[str, str] | None = None,
        extra_betas: list[str] | None = None,
        claude_client: bool = False,
    ) -> RawResult:
        del claude_client
        err = await self._ensure_token()
        if err is not None:
            return err
        url = f"{self.base_url}/v1/messages?beta=true"
        body = dict(payload)
        headers = self._headers(
            stream=stream,
            client_headers=client_headers,
            extra_betas=extra_betas,
        )
        if stream:
            body["stream"] = True
            return await self._call_stream(url, body, headers)
        r = await self._client.post(url, json=body, headers=headers)
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

    async def _call_stream(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> RawResult:
        cm = self._client.stream("POST", url, json=payload, headers=headers)
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

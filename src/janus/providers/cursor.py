"""Cursor provider scaffold.

Ported from 9router ``open-sse/executors/cursor.js`` (headers + endpoint shape).
Full ConnectRPC/protobuf framing is a follow-on; when pointed at an
OpenAI-compatible Cursor bridge this posts chat/completions normally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult, parse_error_body, parse_retry_after

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
DEFAULT_CURSOR_BASE = "https://api2.cursor.sh"


class CursorProvider:
    name = "cursor"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_CURSOR_BASE,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "Cursor/Janus",
            "x-cursor-client-version": "janus-1.2",
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        if self.base_url.endswith("/v1") or "openai" in self.base_url:
            url = f"{self.base_url.rstrip('/')}/chat/completions"
        else:
            # OpenAI-compatible bridge path (protobuf native path is follow-on)
            url = f"{self.base_url}/chat/completions"
        body = {**payload, "stream": stream} if stream else dict(payload)
        if stream:
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

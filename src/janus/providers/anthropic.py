from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=_DEFAULT_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        url = f"{self.base_url}/v1/messages"
        if stream:
            return await self._call_stream(url, payload)
        r = await self._client.post(url, json=payload, headers=self._headers)
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        payload = {**payload, "stream": True}

        async def line_iter() -> AsyncIterator[str]:
            async with self._client.stream("POST", url, json=payload, headers=self._headers) as r:
                async for raw_line in r.aiter_lines():
                    yield raw_line

        return RawResult(status_code=200, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()

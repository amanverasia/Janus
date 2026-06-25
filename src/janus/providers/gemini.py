from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=_DEFAULT_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
        )

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        model = payload.get("model", "gemini-2.0-flash")
        model = model.removeprefix("models/") if isinstance(model, str) else "gemini-2.0-flash"
        if stream:
            url = (
                f"{self.base_url}/v1beta/models/{model}:streamGenerateContent"
                f"?alt=sse&key={self.api_key}"
            )
            return await self._call_stream(url, payload)
        url = f"{self.base_url}/v1beta/models/{model}:generateContent?key={self.api_key}"
        r = await self._client.post(url, json=payload, headers={"Content-Type": "application/json"})
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        async def line_iter() -> AsyncIterator[str]:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as r:
                async for raw_line in r.aiter_lines():
                    yield raw_line

        return RawResult(status_code=200, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()

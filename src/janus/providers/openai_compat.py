from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        url = f"{self.base_url}/chat/completions"
        if stream:
            return await self._call_stream(url, payload)
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, json=payload, headers=self._headers)
            return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        payload = {**payload, "stream": True}

        async def line_iter() -> AsyncIterator[str]:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers
                ) as r:
                    async for raw_line in r.aiter_lines():
                        yield raw_line

        return RawResult(status_code=200, lines=line_iter())

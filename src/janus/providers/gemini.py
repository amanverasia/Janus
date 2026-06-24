from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        model = payload.get("model", "gemini-2.0-flash")
        model = (
            model.removeprefix("models/")
            if isinstance(model, str)
            else "gemini-2.0-flash"
        )
        if stream:
            url = (
                f"{self.base_url}/v1beta/models/{model}:streamGenerateContent"
                f"?alt=sse&key={self.api_key}"
            )
            return await self._call_stream(url, payload)
        url = (
            f"{self.base_url}/v1beta/models/{model}:generateContent"
            f"?key={self.api_key}"
        )
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                url, json=payload, headers={"Content-Type": "application/json"}
            )
            return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        async def line_iter() -> AsyncIterator[str]:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as r:
                    async for raw_line in r.aiter_lines():
                        yield raw_line

        return RawResult(status_code=200, lines=line_iter())

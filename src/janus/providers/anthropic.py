from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult, parse_error_body, parse_retry_after

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)

ANTHROPIC_API_VERSION = "2023-06-01"
# Core betas always sent (matches 9router CLAUDE_API_HEADERS).
ANTHROPIC_BETA_HEADERS = "claude-code-20250219,interleaved-thinking-2025-05-14"
# Extended Claude Code fingerprint (9router CLAUDE_CLI_SPOOF_HEADERS) — adaptive
# effort, prompt caching, advanced tools. Safe on official Anthropic API.
ANTHROPIC_CLI_BETA_HEADERS = (
    "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,"
    "context-management-2025-06-27,prompt-caching-scope-2026-01-05,"
    "advanced-tool-use-2025-11-20,effort-2025-11-24,structured-outputs-2025-12-15,"
    "fast-mode-2026-02-01,redact-thinking-2026-02-12,token-efficient-tools-2026-03-28"
)


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
            "anthropic-version": ANTHROPIC_API_VERSION,
            "anthropic-beta": ANTHROPIC_CLI_BETA_HEADERS,
        }

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        url = f"{self.base_url}/v1/messages"
        if stream:
            return await self._call_stream(url, payload)
        r = await self._client.post(url, json=payload, headers=self._headers)
        if r.status_code >= 400:
            body = await r.aread()
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(body),
                retry_after=parse_retry_after(r.headers),
            )
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        payload = {**payload, "stream": True}
        cm = self._client.stream("POST", url, json=payload, headers=self._headers)
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

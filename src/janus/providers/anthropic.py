"""Anthropic API key provider executor."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from janus.routing.claude_beta import (
    DEFAULT_CLAUDE_BETAS,
    apply_claude_upstream_headers,
    build_claude_upstream_headers,
)

from .base import RawResult, parse_error_body, parse_retry_after

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)

ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_BETA_HEADERS = "claude-code-20250219,interleaved-thinking-2025-05-14"
ANTHROPIC_CLI_BETA_HEADERS = DEFAULT_CLAUDE_BETAS


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            limits=_DEFAULT_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
        )

    def _headers(
        self,
        *,
        stream: bool = False,
        client_headers: dict[str, str] | None = None,
        extra_betas: list[str] | None = None,
        claude_client: bool = False,
    ) -> dict[str, str]:
        base: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        }
        if claude_client:
            return apply_claude_upstream_headers(
                base,
                incoming_headers=client_headers,
                extra_betas=extra_betas,
                oauth=False,
                stream=stream,
                session_seed=self.api_key,
                api_key_auth=True,
            )
        built = build_claude_upstream_headers(
            incoming_headers=client_headers,
            extra_betas=extra_betas,
            oauth=False,
            stream=stream,
            session_seed=self.api_key,
            api_key_auth=True,
        )
        base.update(built)
        base["x-api-key"] = self.api_key
        return base

    async def call(
        self,
        payload: dict[str, Any],
        stream: bool = False,
        *,
        client_headers: dict[str, str] | None = None,
        extra_betas: list[str] | None = None,
        claude_client: bool = False,
    ) -> RawResult:
        url = f"{self.base_url}/v1/messages"
        if claude_client:
            url = f"{url}?beta=true"
        headers = self._headers(
            stream=stream,
            client_headers=client_headers,
            extra_betas=extra_betas,
            claude_client=claude_client,
        )
        if stream:
            return await self._call_stream(url, payload, headers)
        r = await self._client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            body = await r.aread()
            return RawResult(
                status_code=r.status_code,
                json_data=parse_error_body(body),
                retry_after=parse_retry_after(r.headers),
            )
        return RawResult(status_code=r.status_code, json_data=r.json())

    async def _call_stream(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> RawResult:
        payload = {**payload, "stream": True}
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

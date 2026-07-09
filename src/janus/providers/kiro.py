"""Kiro (AWS CodeWhisperer) executor.

Ported from 9router ``open-sse/executors/kiro.js`` (auth headers, multi-host,
social token refresh). Full EventStream→SSE transform is best-effort: when the
upstream returns JSON/SSE lines they are forwarded; binary EventStream chunks
are yielded as opaque lines for bridge setups.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from .base import RawResult, parse_error_body, parse_retry_after
from .oauth_tokens import (
    access_token,
    apply_token_response,
    needs_refresh,
    parse_credential,
    refresh_kiro_social,
    refresh_token,
    serialize_credential,
)

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)

DEFAULT_KIRO_BASES = (
    "https://runtime.us-east-1.kiro.dev",
    "https://codewhisperer.us-east-1.amazonaws.com",
    "https://q.us-east-1.amazonaws.com",
)


class KiroProvider:
    name = "kiro"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_KIRO_BASES[0],
        *,
        auth_method: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self._cred = parse_credential(api_key)
        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        self.auth_method = (
            auth_method
            or (extra.get("authMethod") if isinstance(extra.get("authMethod"), str) else None)
            or self._cred.get("authMethod")
            or "social"
        )
        self.region = (
            region
            if region != "us-east-1" or not isinstance(extra.get("region"), str)
            else str(extra.get("region") or region)
        )
        self.base_url = (base_url or DEFAULT_KIRO_BASES[0]).rstrip("/")
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)

    def credential_blob(self) -> str:
        return serialize_credential(self._cred)

    def _ordered_bases(self) -> list[str]:
        bases = [self.base_url, *DEFAULT_KIRO_BASES]
        # de-dupe preserve order
        seen: set[str] = set()
        ordered: list[str] = []
        for b in bases:
            b = b.rstrip("/")
            if b not in seen:
                seen.add(b)
                ordered.append(b)
        cw_first = self.auth_method in ("api_key", "external_idp", "idc")
        if cw_first:
            amazon = [u for u in ordered if "amazonaws.com" in u]
            others = [u for u in ordered if "amazonaws.com" not in u]
            return amazon + others if amazon else ordered
        return ordered

    async def _ensure_token(self) -> RawResult | None:
        if self.auth_method == "api_key":
            return None
        if not needs_refresh(self._cred):
            return None
        rt = refresh_token(self._cred)
        if not rt:
            return None
        async with self._refresh_lock:
            if not needs_refresh(self._cred):
                return None
            tokens = await refresh_kiro_social(rt, self._client)
            if tokens is None:
                return RawResult(
                    status_code=401,
                    json_data={"error": "Kiro token refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
            if tokens.get("profileArn"):
                extra = (
                    dict(self._cred.get("extra") or {})
                    if isinstance(self._cred.get("extra"), dict)
                    else {}
                )
                extra["profileArn"] = tokens["profileArn"]
                self._cred["extra"] = extra
        return None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.amazon.eventstream",
            "X-Amz-Target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
            "User-Agent": "AWS-SDK-JS/3.0.0 kiro-ide/1.0.0",
            "X-Amz-User-Agent": "aws-sdk-js/3.0.0 kiro-ide/1.0.0",
            "Amz-Sdk-Request": "attempt=1; max=3",
            "Amz-Sdk-Invocation-Id": str(uuid4()),
        }
        token = access_token(self._cred)
        if self.auth_method == "api_key":
            headers["Authorization"] = f"Bearer {token}"
            headers["tokentype"] = "API_KEY"
        elif self.auth_method == "external_idp":
            headers["Authorization"] = f"Bearer {token}"
            headers["TokenType"] = "EXTERNAL_IDP"
        else:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _url_for(self, base: str) -> str:
        if base.endswith("/generateAssistantResponse"):
            return base
        if base.endswith("/v1") or "/chat" in base:
            return f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
        return f"{base}/generateAssistantResponse"

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        err = await self._ensure_token()
        if err is not None:
            return err
        body = dict(payload)
        if stream and "stream" not in body:
            body["stream"] = True
        last: RawResult | None = None
        for base in self._ordered_bases():
            url = self._url_for(base)
            if stream:
                result = await self._call_stream(url, body)
            else:
                r = await self._client.post(url, json=body, headers=self._headers())
                if r.status_code >= 400:
                    result = RawResult(
                        status_code=r.status_code,
                        json_data=parse_error_body(r.content),
                        retry_after=parse_retry_after(r.headers),
                    )
                else:
                    try:
                        data = r.json()
                    except Exception:
                        data = {"error": r.text[:500]}
                    result = RawResult(status_code=r.status_code, json_data=data)
            last = result
            if result.status_code < 400:
                return result
            # try next host on 401/403/5xx
            if result.status_code not in (401, 403) and result.status_code < 500:
                return result
        return last or RawResult(status_code=502, json_data={"error": "Kiro unavailable"})

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

"""Antigravity / Gemini CLI internal generateContent executor.

Ported from 9router antigravity + gemini-cli executors: v1internal envelope,
thinking field strip, Google OAuth refresh.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult, parse_error_body, parse_retry_after
from .oauth_tokens import (
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    GOOGLE_CLI_CLIENT_ID,
    GOOGLE_CLI_CLIENT_SECRET,
    access_token,
    apply_token_response,
    needs_refresh,
    parse_credential,
    refresh_google,
    refresh_token,
    serialize_credential,
)

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
DEFAULT_AG_BASE = "https://cloudcode-pa.googleapis.com"

_THINKING_BLACKLIST = frozenset(
    {
        "output_config",
        "thinking",
        "reasoning_effort",
        "reasoning",
        "enable_thinking",
        "thinking_budget",
        "thinkingConfig",
    }
)


class AntigravityProvider:
    name = "antigravity"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_AG_BASE,
        *,
        project_id: str | None = None,
        credential_expires_at: float | None = None,
        variant: str = "antigravity",
    ) -> None:
        self.base_url = (base_url or DEFAULT_AG_BASE).rstrip("/")
        self._cred = parse_credential(api_key)
        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        self.project_id = project_id or (
            extra.get("projectId") if isinstance(extra.get("projectId"), str) else None
        )
        if not self.project_id:
            self.project_id = None
        self.credential_expires_at = credential_expires_at
        self.variant = variant
        self._refresh_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(limits=_DEFAULT_LIMITS, timeout=_DEFAULT_TIMEOUT)

    def credential_blob(self) -> str:
        return serialize_credential(self._cred)

    async def _ensure_token(self) -> RawResult | None:
        # Older inventory rows may have a refresh token but no expires_at. Treat
        # those credentials as needing one refresh so the returned expiry is
        # captured instead of sending a stale access token forever.
        has_refresh = bool(refresh_token(self._cred))
        has_expiry = (
            self._cred.get("expires_at") is not None
            or self._cred.get("expiresAt") is not None
            or self.credential_expires_at is not None
        )
        if not needs_refresh(self._cred) and (has_expiry or not has_refresh):
            return None
        rt = refresh_token(self._cred)
        if not rt:
            return None
        async with self._refresh_lock:
            if not needs_refresh(self._cred):
                return None
            if self.variant in ("gemini_cli", "gemini-cli"):
                cid, csec = GOOGLE_CLI_CLIENT_ID, GOOGLE_CLI_CLIENT_SECRET
            else:
                cid, csec = ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
            tokens = await refresh_google(rt, self._client, client_id=cid, client_secret=csec)
            if tokens is None:
                return RawResult(
                    status_code=401,
                    json_data={"error": "Google OAuth refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token(self._cred)}",
            "User-Agent": (
                "antigravity/ide/2.1.1 darwin/arm64"
                if self.variant == "antigravity"
                else "gemini-cli"
            ),
        }

    def _sanitize(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        for key in _THINKING_BLACKLIST:
            body.pop(key, None)
        req = body.get("request")
        if isinstance(req, dict):
            for key in _THINKING_BLACKLIST:
                req.pop(key, None)
        return body

    @staticmethod
    def _stable_uuid(seed: str) -> str:
        digest = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
        digest[6] = (digest[6] & 0x0F) | 0x50
        digest[8] = (digest[8] & 0x3F) | 0x80
        value = digest.hex()
        return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"

    def _build_body(self, payload: dict[str, Any], model: str) -> dict[str, Any]:
        body = self._sanitize(payload)
        body.pop("model", None)
        request = body if "request" not in body else body["request"]
        if isinstance(request, dict) and "request" in request:
            request = request["request"]
        seed = self.project_id or access_token(self._cred) or "antigravity"
        conversation = self._stable_uuid(f"antigravity:conversation:{seed}")
        trajectory = self._stable_uuid(f"antigravity:trajectory:{seed}:{model}:agent")
        envelope: dict[str, Any] = {
            "project": self.project_id,
            "model": model,
            "userAgent": "antigravity",
            "requestType": "agent",
            "requestId": f"agent/{conversation}/{int(time.time() * 1000)}/{trajectory}/1",
            "request": request,
        }
        return envelope

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        err = await self._ensure_token()
        if err is not None:
            return err
        raw_model = payload.get("model", "gemini-2.0-flash")
        model = (
            raw_model.removeprefix("models/") if isinstance(raw_model, str) else "gemini-2.0-flash"
        )
        body = self._build_body(payload, model)
        if stream:
            return await self._call_stream(
                f"{self.base_url}/v1internal:streamGenerateContent?alt=sse", body
            )
        r = await self._client.post(
            f"{self.base_url}/v1internal:generateContent", json=body, headers=self._headers()
        )
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

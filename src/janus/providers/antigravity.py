"""Antigravity / Gemini CLI internal generateContent executor.

Ported from 9router antigravity + gemini-cli executors: v1internal envelope,
thinking field strip, Google OAuth refresh.
"""

from __future__ import annotations

import asyncio
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
        variant: str = "antigravity",
    ) -> None:
        self.base_url = (base_url or DEFAULT_AG_BASE).rstrip("/")
        self._cred = parse_credential(api_key)
        raw_extra = self._cred.get("extra")
        extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
        self.project_id = project_id or (
            extra.get("projectId") if isinstance(extra.get("projectId"), str) else None
        )
        self.variant = variant  # antigravity | gemini_cli
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
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token(self._cred)}",
            "User-Agent": "antigravity" if self.variant == "antigravity" else "gemini-cli",
        }
        if self.project_id:
            headers["X-Goog-User-Project"] = self.project_id
        return headers

    def _sanitize(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        for key in _THINKING_BLACKLIST:
            body.pop(key, None)
        req = body.get("request")
        if isinstance(req, dict):
            for key in _THINKING_BLACKLIST:
                req.pop(key, None)
            # Cloak tool names that conflict with Gemini built-ins (light version)
            tools = req.get("tools")
            if isinstance(tools, list):
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    decls = tool.get("functionDeclarations")
                    if not isinstance(decls, list):
                        continue
                    for d in decls:
                        if isinstance(d, dict) and isinstance(d.get("name"), str):
                            # Gemini reserves some names; prefix client tools
                            name = d["name"]
                            if name in ("googleSearch", "codeExecution"):
                                d["name"] = f"client_{name}"
        return body

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        err = await self._ensure_token()
        if err is not None:
            return err
        model = payload.get("model", "gemini-2.0-flash")
        if isinstance(model, str):
            model = model.removeprefix("models/")
        else:
            model = "gemini-2.0-flash"
        body = self._sanitize(payload)
        body.pop("model", None)
        if "request" not in body and ("contents" in body or "generationConfig" in body):
            body = {"request": body, "model": f"models/{model}"}
        elif "model" not in body:
            body["model"] = f"models/{model}"
        if stream:
            url = f"{self.base_url}/v1internal:streamGenerateContent?alt=sse"
            return await self._call_stream(url, body)
        url = f"{self.base_url}/v1internal:generateContent"
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

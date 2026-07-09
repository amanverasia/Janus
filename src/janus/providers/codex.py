"""Codex / ChatGPT Responses API executor.

Ported from 9router ``open-sse/executors/codex.js`` (core transform + OAuth refresh).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import RawResult, parse_error_body, parse_retry_after
from .oauth_tokens import (
    access_token,
    apply_token_response,
    needs_refresh,
    parse_credential,
    refresh_codex,
    refresh_token,
    serialize_credential,
)

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
DEFAULT_CODEX_BASE = "https://chatgpt.com/backend-api/codex"
CODEX_DEFAULT_INSTRUCTIONS = (
    "You are Codex, a coding agent running in the Codex CLI on a user's computer. "
    "Work carefully, use tools when needed, and prefer small correct changes."
)
_SERVER_ID = re.compile(r"^(rs|fc|resp|msg)_")
_HOSTED_TOOLS = frozenset(
    {
        "image_generation",
        "web_search",
        "web_search_preview",
        "file_search",
        "computer",
        "computer_use_preview",
        "code_interpreter",
        "mcp",
        "local_shell",
        "tool_search",
    }
)
_ALLOWLIST = frozenset(
    {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "stream",
        "store",
        "reasoning",
        "service_tier",
        "include",
        "prompt_cache_key",
        "client_metadata",
        "text",
    }
)


class CodexProvider:
    name = "codex"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_CODEX_BASE,
    ) -> None:
        self.base_url = (base_url or DEFAULT_CODEX_BASE).rstrip("/")
        if self.base_url.endswith("/responses"):
            self.base_url = self.base_url[: -len("/responses")]
        self._cred = parse_credential(api_key)
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
            tokens = await refresh_codex(rt, self._client)
            if tokens is None:
                return RawResult(
                    status_code=401,
                    json_data={"error": "Codex OAuth refresh failed — re-auth required"},
                )
            self._cred = apply_token_response(self._cred, tokens)
        return None

    def _headers(self) -> dict[str, str]:
        token = access_token(self._cred)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs/0.136.0",
        }
        workspace = (
            (self._cred.get("extra") or {}).get("workspaceId")
            if isinstance(self._cred.get("extra"), dict)
            else self._cred.get("workspaceId")
        )
        if isinstance(workspace, str) and workspace:
            headers["chatgpt-account-id"] = workspace
            headers["session_id"] = workspace
        return headers

    def _normalize_tools(self, body: dict[str, Any]) -> None:
        tools = body.get("tools")
        if not isinstance(tools, list):
            return
        valid_names: set[str] = set()
        out: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            ttype = tool.get("type") if isinstance(tool.get("type"), str) else ""
            if ttype and ttype != "function":
                if ttype == "custom" or ttype in _HOSTED_TOOLS:
                    out.append(tool)
                continue
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
            raw_name = tool.get("name") if isinstance(tool.get("name"), str) else None
            if not raw_name and fn:
                raw_name = fn.get("name") if isinstance(fn.get("name"), str) else None
            if not raw_name or not str(raw_name).strip():
                continue
            name = str(raw_name).strip()[:128]
            description = ""
            if isinstance(tool.get("description"), str):
                description = tool["description"]
            elif fn and isinstance(fn.get("description"), str):
                description = fn["description"]
            params: dict[str, Any] = {"type": "object", "properties": {}}
            if isinstance(tool.get("parameters"), dict):
                params = tool["parameters"]
            elif fn and isinstance(fn.get("parameters"), dict):
                params = fn["parameters"]
            flat: dict[str, Any] = {"type": "function", "name": name, "parameters": params}
            if description:
                flat["description"] = description
            out.append(flat)
            valid_names.add(name)
        body["tools"] = out
        tc = body.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") == "function":
            n = tc.get("name") if isinstance(tc.get("name"), str) else ""
            if not n or n not in valid_names:
                body.pop("tool_choice", None)

    def _normalize_payload(self, payload: dict[str, Any], stream: bool) -> dict[str, Any]:
        body = {k: v for k, v in payload.items() if k in _ALLOWLIST}
        # Preserve a few optional extras that some clients attach
        for extra in ("metadata", "user", "max_output_tokens"):
            if extra in payload and extra not in body:
                body[extra] = payload[extra]
        body["stream"] = bool(stream or body.get("stream"))
        body.setdefault("store", False)
        instructions = body.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            body["instructions"] = CODEX_DEFAULT_INSTRUCTIONS

        inp = body.get("input")
        if isinstance(inp, str):
            body["input"] = [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": inp}],
                }
            ]
        elif not inp:
            body["input"] = [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "..."}],
                }
            ]

        if isinstance(body.get("input"), list):
            cleaned: list[Any] = []
            for item in body["input"]:
                if isinstance(item, str) and _SERVER_ID.match(item):
                    continue
                if isinstance(item, dict):
                    if item.get("type") == "item_reference":
                        continue
                    item_id = item.get("id")
                    if isinstance(item_id, str) and _SERVER_ID.match(item_id):
                        item = {k: v for k, v in item.items() if k != "id"}
                    if item.get("role") == "system" and (
                        not item.get("type") or item.get("type") == "message"
                    ):
                        item = {**item, "role": "developer"}
                cleaned.append(item)
            body["input"] = cleaned

        self._normalize_tools(body)
        return body

    async def call(self, payload: dict[str, Any], stream: bool = False) -> RawResult:
        err = await self._ensure_token()
        if err is not None:
            return err
        url = f"{self.base_url}/responses"
        body = self._normalize_payload(payload, stream)
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

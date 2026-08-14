"""Codex / ChatGPT Responses API executor.

Ported from 9router ``open-sse/executors/codex.js`` (core transform + OAuth refresh).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
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


def _remember_output_item(
    by_id: dict[str, dict[str, Any]],
    order: list[str],
    anonymous: list[dict[str, Any]],
    item: dict[str, Any],
) -> None:
    """Track streamed ``output_item.done`` payloads for completed-output backfill."""
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        if item_id not in by_id:
            order.append(item_id)
        by_id[item_id] = item
        return
    anonymous.append(item)


def _collected_output_items(
    by_id: dict[str, dict[str, Any]],
    order: list[str],
    anonymous: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [by_id[i] for i in order if i in by_id] + anonymous


def _backfill_response_output(
    response: dict[str, Any],
    done_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """ChatGPT Codex often emits empty/null ``response.completed.output``.

    Real items arrive earlier as ``response.output_item.done``. Rebuild when needed.
    """
    out = response.get("output")
    if isinstance(out, list) and len(out) > 0:
        return response
    if not done_items:
        return response
    patched = dict(response)
    patched["output"] = list(done_items)
    return patched


def _patch_sse_data_line(raw_line: str, done_items: list[dict[str, Any]]) -> str:
    """Rewrite a completed/incomplete SSE data line when ``output`` is empty."""
    stripped = raw_line.strip()
    if not stripped.startswith("data:"):
        return raw_line
    payload_s = stripped[5:].strip()
    if not payload_s or payload_s == "[DONE]":
        return raw_line
    try:
        event = json.loads(payload_s)
    except json.JSONDecodeError:
        return raw_line
    if not isinstance(event, dict):
        return raw_line
    if event.get("type") not in ("response.completed", "response.incomplete"):
        return raw_line
    response = event.get("response")
    if not isinstance(response, dict):
        return raw_line
    patched_response = _backfill_response_output(response, done_items)
    if patched_response is response:
        return raw_line
    patched_event = dict(event)
    patched_event["response"] = patched_response
    prefix = raw_line[: len(raw_line) - len(raw_line.lstrip())]
    return f"{prefix}data: {json.dumps(patched_event, separators=(',', ':'), ensure_ascii=False)}"


# SSE error markers inside 200-OK Codex bodies (9router parity): capacity and
# overload errors must surface as a routable error instead of a broken stream.
_SSE_ERROR_MARKERS = (
    "selected model is at capacity",
    "model_at_capacity",
    "server_is_overloaded",
    "service_unavailable_error",
)
_SSE_OUTPUT_MARKERS = (
    "response.output_text.delta",
    "response.function_call_arguments.delta",
)
_SSE_PEEK_MAX_BYTES = 262_144


def _find_nested_message(value: Any, depth: int = 0) -> str | None:
    if depth > 6:
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_nested_message(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    message = value.get("message")
    if isinstance(message, str) and message.strip():
        return message
    for child in value.values():
        found = _find_nested_message(child, depth + 1)
        if found:
            return found
    return None


def _extract_sse_error_message(lines: list[str]) -> str:
    for raw in lines:
        stripped = raw.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        message = _find_nested_message(event)
        if message:
            return message
    return "Codex model is at capacity or overloaded"


async def _peek_sse_transient_error(
    line_gen: AsyncIterator[str],
) -> tuple[list[str], RawResult | None]:
    """Peek leading SSE lines for capacity/overload errors inside 200-OK bodies.

    ChatGPT Codex reports "Selected model is at capacity" and
    server_is_overloaded as SSE events in an otherwise-200 stream. Detect them
    before committing the stream so the router can rotate to the next account
    (mirrors 9router's _peekSseTransientError). Returns the consumed lines
    (for replay) and an error RawResult when a marker matched.
    """
    buffered: list[str] = []
    seen = 0
    async for raw_line in line_gen:
        buffered.append(raw_line)
        seen += len(raw_line)
        low = raw_line.lower()
        if any(marker in low for marker in _SSE_ERROR_MARKERS):
            return buffered, RawResult(
                status_code=503,
                json_data={
                    "error": {
                        "message": _extract_sse_error_message(buffered),
                        "type": "server_error",
                        "code": "service_unavailable",
                    }
                },
            )
        if any(marker in low for marker in _SSE_OUTPUT_MARKERS) or seen >= _SSE_PEEK_MAX_BYTES:
            break
    return buffered, None


def _usage_limit_retry_after(status_code: int, body: dict[str, Any] | None) -> float | None:
    """Precise reset delay (seconds) from a Codex ``usage_limit_reached`` 429 body.

    ChatGPT's Codex backend reports quota exhaustion in the JSON body
    (``resets_at`` epoch seconds / ``resets_in_seconds``) without sending a
    Retry-After header. Mirrors 9router's codex executor ``parseError`` so the
    fallback handler can cool the account down until the reported reset
    (capped by RETRY_AFTER_CAP_S in the routing layer).
    """
    if status_code != 429 or not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict) or err.get("type") != "usage_limit_reached":
        return None
    resets_at = err.get("resets_at")
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        delay = float(resets_at) - time.time()
        if delay > 0:
            return delay
    resets_in = err.get("resets_in_seconds")
    if isinstance(resets_in, (int, float)) and not isinstance(resets_in, bool) and resets_in > 0:
        return float(resets_in)
    return None


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
        del stream  # Codex upstream always requires stream=true
        body = {k: v for k, v in payload.items() if k in _ALLOWLIST}
        for extra in ("metadata", "user"):
            if extra in payload and extra not in body:
                body[extra] = payload[extra]
        body.pop("max_output_tokens", None)
        body["stream"] = True
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
        # ChatGPT Codex rejects non-streaming requests ("Stream must be set to true").
        body = self._normalize_payload(payload, stream=True)
        streamed = await self._call_stream(url, body)
        if stream or streamed.lines is None:
            return streamed
        return await self._buffer_forced_stream(streamed)

    async def _buffer_forced_stream(self, streamed: RawResult) -> RawResult:
        """Consume forced upstream SSE into a single Responses JSON object."""
        assert streamed.lines is not None
        final: dict[str, Any] | None = None
        last_error: dict[str, Any] | None = None
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        anonymous: list[dict[str, Any]] = []
        async for raw_line in streamed.lines:
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")
            if etype == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict):
                    _remember_output_item(by_id, order, anonymous, item)
            if etype == "response.failed" or event.get("error"):
                err = event.get("error") if isinstance(event.get("error"), dict) else event
                last_error = err if isinstance(err, dict) else {"error": err}
                continue
            response = event.get("response")
            if isinstance(response, dict) and (
                etype in ("response.completed", "response.incomplete") or final is None
            ):
                final = response
        if final is not None:
            done_items = _collected_output_items(by_id, order, anonymous)
            final = _backfill_response_output(final, done_items)
            return RawResult(status_code=streamed.status_code, json_data=final)
        if last_error is not None:
            return RawResult(status_code=400, json_data=last_error)
        return RawResult(
            status_code=streamed.status_code,
            json_data={"error": "Codex stream ended without a completed response"},
        )

    async def _call_stream(self, url: str, payload: dict[str, Any]) -> RawResult:
        cm = self._client.stream("POST", url, json=payload, headers=self._headers())
        r = await cm.__aenter__()
        if r.status_code >= 400:
            body = await r.aread()
            await cm.__aexit__(None, None, None)
            error_body = parse_error_body(body)
            return RawResult(
                status_code=r.status_code,
                json_data=error_body,
                retry_after=parse_retry_after(r.headers)
                or _usage_limit_retry_after(r.status_code, error_body),
            )

        line_gen = r.aiter_lines()
        buffered, sse_error = await _peek_sse_transient_error(line_gen)
        if sse_error is not None:
            await cm.__aexit__(None, None, None)
            return sse_error

        async def _replay_lines() -> AsyncIterator[str]:
            for line in buffered:
                yield line
            async for line in line_gen:
                yield line

        async def line_iter() -> AsyncIterator[str]:
            by_id: dict[str, dict[str, Any]] = {}
            order: list[str] = []
            anonymous: list[dict[str, Any]] = []
            try:
                async for raw_line in _replay_lines():
                    stripped = raw_line.strip()
                    if stripped.startswith("data:"):
                        payload_s = stripped[5:].strip()
                        if payload_s and payload_s != "[DONE]":
                            try:
                                ev = json.loads(payload_s)
                            except json.JSONDecodeError:
                                ev = None
                            if isinstance(ev, dict):
                                if ev.get("type") == "response.output_item.done":
                                    item = ev.get("item")
                                    if isinstance(item, dict):
                                        _remember_output_item(by_id, order, anonymous, item)
                                if ev.get("type") in (
                                    "response.completed",
                                    "response.incomplete",
                                ):
                                    done_items = _collected_output_items(by_id, order, anonymous)
                                    yield _patch_sse_data_line(raw_line, done_items)
                                    continue
                    yield raw_line
            finally:
                await cm.__aexit__(None, None, None)

        return RawResult(status_code=r.status_code, lines=line_iter())

    async def close(self) -> None:
        await self._client.aclose()

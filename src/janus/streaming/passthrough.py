"""OpenAI-compatible SSE passthrough — ported from 9router ``utils/stream.js``.

Mode: PASSTHROUGH (same client/provider wire format).

Responsibilities:
- Correct SSE framing from httpx ``aiter_lines()`` (restore blank-line terminators)
- Normalize chunk fields (``object``, ``created``, invalid ``id``)
- Strip Azure non-standard fields
- Extract usage for accounting
- Guarantee stream termination: non-null ``finish_reason`` + ``data: [DONE]``
  (clients like pi/opencode fail with "Stream ended without finish_reason")
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from janus.canonical.models import Usage
from janus.streaming.sse import encode_done, encode_sse
from janus.streaming.usage import StreamUsageTracker

logger = logging.getLogger(__name__)

# Providers that reject the OpenAI [DONE] sentinel (9router gemini-family list)
_NO_DONE_PROVIDERS = frozenset({"antigravity", "gemini", "vertex", "gemini_cli", "gemini-cli"})


def parse_sse_data_line(line: str) -> tuple[str, Any] | None:
    """Return (kind, payload) for a single SSE line.

    kind is ``\"data\"`` with payload str/dict/\"[DONE]\", or ``\"meta\"`` for
    event:/id:/comment lines. Blank lines return None.
    """
    if line == "":
        return None
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith(":"):
        return ("meta", stripped)
    if stripped.startswith("event:") or stripped.startswith("id:") or stripped.startswith("retry:"):
        return ("meta", stripped)
    if stripped.startswith("data:"):
        data = stripped[5:].lstrip()
        if data == "[DONE]":
            return ("data", "[DONE]")
        try:
            return ("data", json.loads(data))
        except json.JSONDecodeError:
            # Non-JSON data — keep raw string (will be dropped if not useful)
            return ("data", data)
    # Bare JSON (some upstreams omit data: prefix)
    if stripped.startswith("{"):
        try:
            return ("data", json.loads(stripped))
        except json.JSONDecodeError:
            return None
    if stripped == "[DONE]":
        return ("data", "[DONE]")
    return ("meta", stripped)


def fix_invalid_id(parsed: dict[str, Any]) -> bool:
    """Mirror 9router ``fixInvalidId`` — replace too-short / generic ids."""
    cid = parsed.get("id")
    if not isinstance(cid, str):
        return False
    if cid in ("chat", "completion") or len(cid) < 8:
        fallback = str(int(time.time() * 1000))
        ext = parsed.get("extend_fields")
        if isinstance(ext, dict):
            fallback = str(ext.get("requestId") or ext.get("traceId") or fallback)
        parsed["id"] = f"chatcmpl-{fallback}"
        return True
    return False


def normalize_openai_chunk(parsed: dict[str, Any]) -> dict[str, Any]:
    """Inject required OpenAI stream fields; strip Azure / empty-tool noise."""
    if "choices" in parsed:
        if not parsed.get("object"):
            parsed["object"] = "chat.completion.chunk"
        if not parsed.get("created"):
            parsed["created"] = int(time.time())
    parsed.pop("prompt_filter_results", None)
    parsed.pop("content_filter_results", None)

    choices = parsed.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            choice.pop("content_filter_results", None)
            delta = choice.get("delta")
            if isinstance(delta, dict):
                # Empty tool_calls arrays break AI SDK reasoning tracking
                # (delta.tool_calls != null is true for []).
                tcs = delta.get("tool_calls")
                if isinstance(tcs, list) and len(tcs) == 0:
                    del delta["tool_calls"]

    fix_invalid_id(parsed)
    return parsed


def has_valuable_content(parsed: dict[str, Any]) -> bool:
    """Keep only chunks that carry content, tools, role, finish, or usage."""
    if parsed.get("usage"):
        return True
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        # Non-OpenAI-shaped object: keep (e.g. error payloads we already parsed)
        return True
    choice = choices[0] if isinstance(choices[0], dict) else {}
    if choice.get("finish_reason") not in (None, ""):
        return True
    raw_delta = choice.get("delta")
    delta = raw_delta if isinstance(raw_delta, dict) else {}
    if delta.get("role"):
        return True
    content = delta.get("content")
    if content not in (None, ""):
        return True
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning not in (None, ""):
        return True
    tcs = delta.get("tool_calls")
    if isinstance(tcs, list) and len(tcs) > 0:
        return True
    return False


def _has_finish_reason(parsed: dict[str, Any]) -> bool:
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    fr = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
    return fr is not None and fr != ""


def _finish_chunk(model: str = "", usage: Usage | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": f"chatcmpl-janus-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    if usage is not None and (usage.input_tokens or usage.output_tokens):
        data["usage"] = {
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
        }
    return data


def format_openai_sse(data: dict[str, Any] | str) -> bytes:
    if data == "[DONE]":
        return encode_done()
    if isinstance(data, str):
        return f"data: {data}\n\n".encode()
    return encode_sse(data)


async def openai_passthrough_stream(
    lines: AsyncIterator[str],
    *,
    tracker: StreamUsageTracker | None = None,
    model: str = "",
    provider: str = "",
    ensure_finish: bool = True,
    ensure_done: bool = True,
) -> AsyncIterator[bytes]:
    """Re-emit OpenAI Chat Completions SSE with 9router-style normalization.

    ``lines`` is an async iterator of text lines as from httpx ``aiter_lines()``
    (no trailing newline; empty string = blank SSE separator).
    """
    saw_finish = False
    saw_done = False
    pending_open = False  # emitted data line without trailing blank yet
    skip_done = provider in _NO_DONE_PROVIDERS

    async for raw in lines:
        if raw == "":
            if pending_open:
                yield b"\n"
                pending_open = False
            continue

        parsed = parse_sse_data_line(raw)
        if parsed is None:
            continue
        kind, payload = parsed

        if kind == "meta":
            # Close any open data event first so framing stays valid
            if pending_open:
                yield b"\n"
                pending_open = False
            yield f"{payload}\n".encode()
            continue

        # kind == data
        if payload == "[DONE]":
            if pending_open:
                yield b"\n"
                pending_open = False
            if not skip_done and not saw_done:
                yield encode_done()
                saw_done = True
            continue

        if isinstance(payload, str):
            # Non-JSON data — drop garbage (9router skips non-JSON data lines)
            logger.debug("Dropping non-JSON SSE data: %s", payload[:120])
            continue

        if not isinstance(payload, dict):
            continue

        chunk = normalize_openai_chunk(payload)

        # Feed usage tracker even for empty chunks so accounting stays accurate
        if tracker is not None:
            try:
                tracker.feed(json.dumps(chunk, ensure_ascii=False))
            except Exception:
                pass

        if _has_finish_reason(chunk):
            saw_finish = True
            # Attach accumulated usage to finish chunk when missing (9router)
            if tracker is not None and not (
                isinstance(chunk.get("usage"), dict)
                and chunk["usage"].get("prompt_tokens") is not None
            ):
                usage = tracker.get_usage()
                if usage.input_tokens or usage.output_tokens:
                    chunk["usage"] = {
                        "prompt_tokens": usage.input_tokens,
                        "completion_tokens": usage.output_tokens,
                        "total_tokens": usage.input_tokens + usage.output_tokens,
                    }

        if not has_valuable_content(chunk):
            continue

        # Emit as `data: {...}\n` then wait for blank separator (or force one later)
        text = json.dumps(chunk, separators=(",", ":"), ensure_ascii=False)
        yield f"data: {text}\n".encode()
        pending_open = True

    if pending_open:
        yield b"\n"

    usage_final = tracker.get_usage() if tracker is not None else None
    if ensure_finish and not saw_finish:
        yield format_openai_sse(_finish_chunk(model, usage_final))
        saw_finish = True
    if ensure_done and not saw_done and not skip_done:
        yield encode_done()

    if tracker is not None:
        tracker.finish()


async def generic_sse_passthrough(
    lines: AsyncIterator[str],
    *,
    tracker: StreamUsageTracker | None = None,
) -> AsyncIterator[bytes]:
    """Framing-correct passthrough for non-OpenAI SSE (Anthropic/Gemini/etc.)."""
    pending_open = False
    async for raw in lines:
        if raw == "":
            if pending_open:
                yield b"\n"
                pending_open = False
            continue
        stripped = raw.strip()
        if stripped.startswith("data:"):
            data = stripped[5:].lstrip()
            if tracker is not None and data and data != "[DONE]":
                tracker.feed(data)
        yield f"{raw}\n".encode()
        pending_open = True
    if pending_open:
        yield b"\n"
    if tracker is not None:
        tracker.finish()

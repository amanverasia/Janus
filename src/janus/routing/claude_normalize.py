"""Normalize Claude Code / Cowork beta shapes for Anthropic Messages API.

Ported from 9router ``open-sse/translator/formats/claude.js``
``normalizeClaudePassthrough``.
"""

from __future__ import annotations

import re
from typing import Any

_ADAPTIVE_UNSUPPORTED = re.compile(r"haiku", re.I)
_CLAUDE_SIG_PREFIXES = ("",)  # signatures are opaque; empty allowed only as placeholder


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _is_valid_claude_signature(sig: Any) -> bool:
    if not isinstance(sig, str) or not sig:
        return False
    # Foreign model signatures (OpenAI/Gemini combo history) usually look different;
    # Anthropic rejects non-Claude signatures. Keep any non-empty signature that
    # does not look like a JSON blob from another stack.
    return not sig.startswith("{") and "openai" not in sig.lower()


def normalize_claude_passthrough(body: dict[str, Any], model: str = "") -> dict[str, Any]:
    """Mutate and return body so Anthropic accepts Claude Code wire shapes."""
    if not isinstance(body, dict):
        return body

    thinking = body.get("thinking")
    if (
        isinstance(thinking, dict)
        and thinking.get("type") == "adaptive"
        and _ADAPTIVE_UNSUPPORTED.search(model or "")
    ):
        body["thinking"] = {"type": "enabled", "budget_tokens": 10000}

    if _ADAPTIVE_UNSUPPORTED.search(model or ""):
        out_cfg = body.get("output_config")
        if isinstance(out_cfg, dict) and "effort" in out_cfg:
            out_cfg = dict(out_cfg)
            out_cfg.pop("effort", None)
            if out_cfg:
                body["output_config"] = out_cfg
            else:
                body.pop("output_config", None)

    messages = body.get("messages")
    if isinstance(messages, list):
        system_blocks: list[dict[str, str]] = []
        kept_messages: list[Any] = []
        for msg in messages:
            if not isinstance(msg, dict):
                kept_messages.append(msg)
                continue
            if msg.get("role") == "system":
                text = _content_text(msg.get("content"))
                if text.strip():
                    system_blocks.append({"type": "text", "text": text})
                continue
            kept_messages.append(msg)

        if system_blocks:
            existing = body.get("system")
            if isinstance(existing, list):
                existing_blocks = [b for b in existing if isinstance(b, dict)]
            elif isinstance(existing, str) and existing.strip():
                existing_blocks = [{"type": "text", "text": existing}]
            else:
                existing_blocks = []
            body["system"] = existing_blocks + system_blocks
            body["messages"] = kept_messages
            messages = kept_messages

    thinking_enabled = (
        isinstance(body.get("thinking"), dict) and body["thinking"].get("type") == "enabled"
    )
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            has_tool_use = False
            has_kept_thinking = False
            kept: list[Any] = []
            for block in content:
                if not isinstance(block, dict):
                    kept.append(block)
                    continue
                btype = block.get("type")
                if btype in ("thinking", "redacted_thinking"):
                    if _is_valid_claude_signature(block.get("signature")):
                        has_kept_thinking = True
                        kept.append(block)
                    continue
                if btype == "tool_use":
                    has_tool_use = True
                kept.append(block)
            msg["content"] = kept
            if thinking_enabled and not has_kept_thinking and has_tool_use:
                msg["content"].insert(
                    0,
                    {
                        "type": "thinking",
                        "thinking": " ",
                        "signature": "janus-placeholder",
                    },
                )

    return body

"""Detect coding-tool clients and decide when native passthrough is safe.

Ported from 9router ``open-sse/utils/clientDetector.js`` — used to:
- skip unnecessary translation when client ecosystem matches provider
- apply client-specific quirks (e.g. Claude beta shapes)
"""

from __future__ import annotations

from typing import Any

NATIVE_PAIRS: dict[str, frozenset[str]] = {
    "claude": frozenset({"anthropic", "claude"}),
    "gemini-cli": frozenset({"gemini", "gemini-cli"}),
    "antigravity": frozenset({"antigravity"}),
    "codex": frozenset({"codex", "openai"}),
    "github-copilot": frozenset({"github_copilot", "github-copilot", "github"}),
}


def detect_client_tool(
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> str | None:
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    body = body or {}

    ua = headers.get("user-agent", "").lower()
    x_app = headers.get("x-app", "").lower()
    openai_intent = headers.get("openai-intent", "").lower()
    initiator = headers.get("x-initiator", "").lower()

    if body.get("userAgent") == "antigravity":
        return "antigravity"

    if "githubcopilotchat" in ua or openai_intent == "conversation-panel" or initiator == "user":
        return "github-copilot"

    if "claude-cli" in ua or "claude-code" in ua or x_app == "cli":
        return "claude"

    if "gemini-cli" in ua:
        return "gemini-cli"

    if "codex-cli" in ua:
        return "codex"

    if "deepseek-tui" in ua:
        return "deepseek-tui"

    return None


def is_native_passthrough(client_tool: str | None, provider_id: str) -> bool:
    if not client_tool:
        return False
    native = NATIVE_PAIRS.get(client_tool)
    if not native:
        return False
    normalized = provider_id
    if provider_id.startswith("anthropic-compatible"):
        normalized = "anthropic"
    return normalized in native

"""Per-model capability resolution (vision, reasoning, thinkingFormat, …).

Inspired by 9router ``open-sse/providers/capabilities.js``.
Fallback order:
  1. exact model id override
  2. glob-ish pattern match (ordered specific → generic)
  3. provider-prefix defaults from catalog
  4. DEFAULT_CAPABILITIES
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

DEFAULT_CAPABILITIES: dict[str, Any] = {
    "vision": False,
    "pdf": False,
    "audio_input": False,
    "tool_use": True,
    "search": False,
    "reasoning": False,
    "thinking_format": None,
    "thinking_can_disable": True,
    "thinking_range": None,
    "context_window": 200_000,
    "max_output": 64_000,
}

MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "gpt-4o": {"vision": True, "pdf": True, "tool_use": True, "search": True},
    "gpt-4.1": {"vision": True, "pdf": True, "tool_use": True},
    "gpt-4.1-mini": {"vision": True, "tool_use": True},
    "o3": {
        "vision": True,
        "reasoning": True,
        "thinking_format": "openai",
        "thinking_can_disable": False,
    },
    "o4-mini": {
        "vision": True,
        "reasoning": True,
        "thinking_format": "openai",
        "thinking_can_disable": False,
    },
    "claude-opus-4-20250514": {
        "vision": True,
        "pdf": True,
        "reasoning": True,
        "thinking_format": "claude-budget",
        "search": True,
        "context_window": 200_000,
    },
    "claude-sonnet-4-20250514": {
        "vision": True,
        "pdf": True,
        "reasoning": True,
        "thinking_format": "claude-budget",
        "search": True,
    },
    "claude-3-5-sonnet-20241022": {
        "vision": True,
        "pdf": True,
        "tool_use": True,
    },
}

# Ordered specific → generic. First match wins.
PATTERN_CAPABILITIES: list[tuple[str, dict[str, Any]]] = [
    (
        "claude-*-4.6*",
        {
            "vision": True,
            "pdf": True,
            "reasoning": True,
            "thinking_format": "claude-adaptive",
            "search": True,
            "context_window": 1_000_000,
        },
    ),
    (
        "claude-*-4*",
        {
            "vision": True,
            "pdf": True,
            "reasoning": True,
            "thinking_format": "claude-budget",
            "search": True,
        },
    ),
    (
        "claude-*",
        {"vision": True, "pdf": True, "tool_use": True},
    ),
    (
        "gpt-5*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "openai",
            "search": True,
            "context_window": 400_000,
            "max_output": 128_000,
        },
    ),
    (
        "gpt-4o*",
        {"vision": True, "pdf": True, "tool_use": True, "search": True},
    ),
    (
        "grok*image*",
        {"vision": True, "tool_use": False, "reasoning": False},
    ),
    (
        "grok-code*",
        {
            "reasoning": True,
            "thinking_format": "openai",
            "tool_use": True,
            "context_window": 256_000,
        },
    ),
    (
        "grok-4*",
        {
            "vision": True,
            "reasoning": True,
            "search": True,
            "thinking_format": "openai",
            "tool_use": True,
            "context_window": 256_000,
        },
    ),
    (
        "grok-3*",
        {
            "vision": True,
            "reasoning": True,
            "search": True,
            "thinking_format": "openai",
            "tool_use": True,
            "context_window": 131_072,
        },
    ),
    (
        "grok*",
        {
            "vision": True,
            "reasoning": True,
            "search": True,
            "thinking_format": "openai",
            "tool_use": True,
            "context_window": 256_000,
        },
    ),
    (
        "o3*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "openai",
            "thinking_can_disable": False,
        },
    ),
    (
        "o4*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "openai",
            "thinking_can_disable": False,
        },
    ),
    (
        "gemini-3*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "gemini-level",
            "thinking_can_disable": False,
            "search": True,
        },
    ),
    (
        "gemini-2*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "gemini-budget",
            "search": True,
        },
    ),
    (
        "gemini*",
        {"vision": True, "tool_use": True},
    ),
    (
        "deepseek-v4*",
        {
            "reasoning": True,
            "thinking_format": "deepseek",
            "tool_use": True,
            "context_window": 1_000_000,
            "max_output": 384_000,
        },
    ),
    (
        "deepseek-reasoner*",
        {
            "reasoning": True,
            "thinking_format": "deepseek",
            "thinking_can_disable": False,
            "tool_use": True,
            "context_window": 128_000,
        },
    ),
    (
        "deepseek-chat*",
        {"tool_use": True, "context_window": 128_000},
    ),
    (
        "deepseek*",
        {"reasoning": True, "thinking_format": "deepseek", "tool_use": True},
    ),
    (
        "qwen*",
        {"reasoning": True, "thinking_format": "qwen", "tool_use": True},
    ),
    (
        "kimi*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "kimi",
            "thinking_can_disable": False,
        },
    ),
    (
        "minimax*",
        {"reasoning": True, "thinking_format": "minimax"},
    ),
    (
        "glm*",
        {"reasoning": True, "thinking_format": "zai"},
    ),
    (
        "codex*",
        {
            "vision": True,
            "reasoning": True,
            "thinking_format": "openai",
            "thinking_can_disable": False,
        },
    ),
    (
        "mimo-v2.5-pro*",
        {
            "vision": True,
            "reasoning": True,
            "tool_use": True,
            "thinking_format": "openai",
            "context_window": 256_000,
            "max_output": 32_000,
        },
    ),
    (
        "mimo-v2.5*",
        {
            "vision": True,
            "reasoning": True,
            "tool_use": True,
            "context_window": 256_000,
            "max_output": 32_000,
        },
    ),
    (
        "mimo-v2*",
        {
            "vision": True,
            "tool_use": True,
            "context_window": 128_000,
            "max_output": 16_000,
        },
    ),
    (
        "mimo-auto*",
        {
            "vision": False,
            "tool_use": True,
            "context_window": 128_000,
            "max_output": 16_000,
        },
    ),
]


def _match_pattern(pattern: str, model: str) -> bool:
    return fnmatch.fnmatch(model.lower(), pattern.lower())


def get_model_capabilities(
    provider: str | None,
    model: str,
    *,
    provider_caps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve capabilities for a model, merged over DEFAULT_CAPABILITIES."""
    clean = re.sub(r"\([^()]+\)\s*$", "", model).strip()
    # Strip provider prefix if present
    if "/" in clean:
        _, clean = clean.split("/", 1)

    result = dict(DEFAULT_CAPABILITIES)

    if provider_caps:
        result.update({k: v for k, v in provider_caps.items() if v is not None})

    for pattern, caps in PATTERN_CAPABILITIES:
        if _match_pattern(pattern, clean):
            result.update(caps)
            break

    if clean in MODEL_CAPABILITIES:
        result.update(MODEL_CAPABILITIES[clean])

    # provider-specific light overrides
    if provider:
        p = provider.lower()
        if p in ("anthropic", "claude") and result.get("thinking_format") is None:
            if result.get("reasoning"):
                result["thinking_format"] = "claude-budget"
        if p in ("gemini", "antigravity", "gemini-cli") and result.get("thinking_format") is None:
            if result.get("reasoning"):
                result["thinking_format"] = "gemini-budget"
        if p in ("openai", "codex", "github_copilot", "github-copilot", "xai") and result.get(
            "thinking_format"
        ) is None:
            if result.get("reasoning"):
                result["thinking_format"] = "openai"

    return result

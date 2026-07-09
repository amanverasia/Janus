"""Strip built-in tools when equivalent MCP tools are present.

Ported from 9router ``open-sse/utils/toolDeduper.js``.
"""

from __future__ import annotations

import re
from typing import Any

_DEDUP_RULES: list[dict[str, list[str | re.Pattern[str]]]] = [
    {
        "triggers": ["mcp__exa__web_search_exa", "mcp__exa__web_fetch_exa"],
        "strip": ["WebSearch", "WebFetch", "mcp__workspace__web_fetch"],
    },
    {
        "triggers": ["mcp__tavily__tavily_search", "mcp__tavily__tavily_extract"],
        "strip": ["WebSearch", "WebFetch", "mcp__workspace__web_fetch"],
    },
    {
        "triggers": [re.compile(r"^mcp__browsermcp__")],
        "strip": [re.compile(r"^mcp__Claude_in_Chrome__")],
    },
]


def _tool_name(tool: dict[str, Any]) -> str:
    if not isinstance(tool, dict):
        return ""
    name = tool.get("name")
    if isinstance(name, str) and name:
        return name
    fn = tool.get("function")
    if isinstance(fn, dict):
        n = fn.get("name")
        if isinstance(n, str):
            return n
    return ""


def _matches(name: str, pattern: str | re.Pattern[str]) -> bool:
    if isinstance(pattern, str):
        return name == pattern
    return bool(pattern.search(name))


def dedupe_tools(tools: list[Any] | None) -> tuple[list[Any], list[str]]:
    if not isinstance(tools, list) or not tools:
        return tools or [], []
    names = [_tool_name(t) if isinstance(t, dict) else "" for t in tools]
    to_strip: set[str] = set()
    for rule in _DEDUP_RULES:
        has_trigger = any(any(_matches(n, p) for p in rule["triggers"]) for n in names if n)
        if not has_trigger:
            continue
        for n in names:
            if n and any(_matches(n, p) for p in rule["strip"]):
                to_strip.add(n)
    if not to_strip:
        return tools, []
    out = [t for t in tools if not (isinstance(t, dict) and _tool_name(t) in to_strip)]
    return out, sorted(to_strip)

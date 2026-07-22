"""OAuth tool-name remap for Claude Code fingerprint compatibility."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

_OAUTH_TOOL_RENAME: dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "task": "Task",
    "webfetch": "WebFetch",
    "todowrite": "TodoWrite",
    "question": "Question",
    "skill": "Skill",
    "ls": "LS",
    "todoread": "TodoRead",
    "notebookedit": "NotebookEdit",
}


def remap_oauth_tool_names(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Rename tools to Claude Code TitleCase equivalents; return per-request reverse map."""
    reverse_map: dict[str, str] = {}
    out = deepcopy(body)

    tools = out.get("tools")
    if isinstance(tools, list):
        new_tools: list[Any] = []
        for tool in tools:
            if not isinstance(tool, dict):
                new_tools.append(tool)
                continue
            if tool.get("type"):
                new_tools.append(tool)
                continue
            name = tool.get("name")
            if not isinstance(name, str):
                new_tools.append(tool)
                continue
            renamed = _OAUTH_TOOL_RENAME.get(name)
            if renamed and renamed != name:
                reverse_map.setdefault(renamed, name)
                new_tools.append({**tool, "name": renamed})
            else:
                new_tools.append(tool)
        out["tools"] = new_tools

    tool_choice = out.get("tool_choice")
    if isinstance(tool_choice, dict):
        name = tool_choice.get("name")
        if isinstance(name, str):
            renamed = _OAUTH_TOOL_RENAME.get(name)
            if renamed and renamed != name:
                reverse_map.setdefault(renamed, name)
                out["tool_choice"] = {**tool_choice, "name": renamed}

    messages = out.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") not in ("tool_use", "tool_reference"):
                    continue
                name = block.get("name")
                if not isinstance(name, str):
                    continue
                renamed = _OAUTH_TOOL_RENAME.get(name)
                if renamed and renamed != name:
                    reverse_map.setdefault(renamed, name)
                    block["name"] = renamed

    return out, reverse_map


def restore_oauth_tool_names(data: dict[str, Any], reverse_map: dict[str, str]) -> dict[str, Any]:
    if not reverse_map:
        return data
    out = deepcopy(data)
    content = out.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in ("tool_use", "tool_reference"):
                continue
            name = block.get("name")
            if isinstance(name, str) and name in reverse_map:
                block["name"] = reverse_map[name]
    return out


def restore_oauth_tool_names_stream_line(line: str, reverse_map: dict[str, str]) -> str:
    if not reverse_map or not line.startswith("data:"):
        return line
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return line
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return line
    if not isinstance(chunk, dict):
        return line
    changed = False
    block = chunk.get("content_block")
    if isinstance(block, dict):
        name = block.get("name")
        if isinstance(name, str) and name in reverse_map:
            block["name"] = reverse_map[name]
            changed = True
    delta = chunk.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
        pass
    message = chunk.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name in reverse_map:
                    item["name"] = reverse_map[name]
                    changed = True
    if not changed:
        raw = json.dumps(chunk, separators=(",", ":"), ensure_ascii=False)
        for upstream, original in reverse_map.items():
            if upstream in raw:
                raw = raw.replace(f'"name":"{upstream}"', f'"name":"{original}"')
                raw = raw.replace(f'"name": "{upstream}"', f'"name": "{original}"')
                changed = True
        if changed:
            return f"data: {raw}"
    return f"data: {json.dumps(chunk, separators=(',', ':'), ensure_ascii=False)}"

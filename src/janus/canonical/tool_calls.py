from __future__ import annotations

import re
from typing import Any

from janus.canonical.models import ContentPart, Message, Role, ToolResult, ToolUse

TOOL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def sanitize_tool_id(tool_id: str | None) -> str | None:
    if not tool_id:
        return None
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", tool_id)
    return sanitized if sanitized else None


def generate_tool_call_id(msg_index: int, tc_index: int, tool_name: str = "") -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", tool_name) if tool_name else ""
    suffix = f"_{safe_name}" if safe_name else ""
    return f"call_msg{msg_index}_tc{tc_index}{suffix}"


def get_tool_call_ids(msg: Message) -> list[str]:
    if msg.role != Role.ASSISTANT:
        return []
    if not isinstance(msg.content, list):
        return []
    return [p.id for p in msg.content if isinstance(p, ToolUse) and p.id]


def has_tool_results(msg: Message | None, tool_call_ids: list[str]) -> bool:
    if msg is None or not tool_call_ids:
        return False
    if msg.role == Role.TOOL and isinstance(msg.content, list):
        return any(
            isinstance(p, ToolResult) and p.tool_use_id in tool_call_ids for p in msg.content
        )
    if msg.role == Role.USER and isinstance(msg.content, list):
        return any(
            isinstance(p, ToolResult) and p.tool_use_id in tool_call_ids for p in msg.content
        )
    return False


def _ensure_part_tool_ids(
    part: ContentPart,
    msg_index: int,
    tc_index: int,
) -> tuple[ContentPart, int]:
    if isinstance(part, ToolUse):
        tool_id = part.id
        if not tool_id or not TOOL_ID_PATTERN.match(tool_id):
            sanitized = sanitize_tool_id(tool_id)
            tool_id = sanitized or generate_tool_call_id(msg_index, tc_index, part.name)
        next_index = tc_index + 1
        if tool_id != part.id:
            return part.model_copy(update={"id": tool_id}), next_index
        return part, next_index
    if isinstance(part, ToolResult):
        tool_use_id = part.tool_use_id
        if not tool_use_id or not TOOL_ID_PATTERN.match(tool_use_id):
            sanitized = sanitize_tool_id(tool_use_id)
            tool_use_id = sanitized or generate_tool_call_id(msg_index, tc_index)
        if tool_use_id != part.tool_use_id:
            return part.model_copy(update={"tool_use_id": tool_use_id}), tc_index
    return part, tc_index


def _ensure_message_tool_ids(msg: Message, msg_index: int) -> Message:
    if not isinstance(msg.content, list):
        return msg
    tc_index = 0
    new_parts: list[ContentPart] = []
    for part in msg.content:
        updated, tc_index = _ensure_part_tool_ids(part, msg_index, tc_index)
        new_parts.append(updated)
    if new_parts == msg.content:
        return msg
    return Message(role=msg.role, content=new_parts)


def ensure_tool_call_ids(messages: list[Message]) -> list[Message]:
    return [_ensure_message_tool_ids(msg, i) for i, msg in enumerate(messages)]


def fix_missing_tool_responses(messages: list[Message]) -> list[Message]:
    new_messages: list[Message] = []
    for i, msg in enumerate(messages):
        new_messages.append(msg)
        tool_call_ids = get_tool_call_ids(msg)
        if not tool_call_ids:
            continue
        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        if next_msg is not None and has_tool_results(next_msg, tool_call_ids):
            continue
        for tool_id in tool_call_ids:
            new_messages.append(
                Message(
                    role=Role.TOOL,
                    content=[ToolResult(tool_use_id=tool_id, content="")],
                )
            )
    return new_messages


def prepare_tool_messages(messages: list[Message]) -> list[Message]:
    return fix_missing_tool_responses(ensure_tool_call_ids(messages))


def fix_missing_tool_responses_openai(messages: list[dict[str, Any]]) -> None:
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            i += 1
            continue
        tool_call_ids = [tc["id"] for tc in msg["tool_calls"] if tc.get("id")]
        responded_ids: set[str] = set()
        insert_position = i + 1
        for j in range(i + 1, len(messages)):
            next_msg = messages[j]
            if next_msg.get("role") == "tool" and next_msg.get("tool_call_id"):
                responded_ids.add(str(next_msg["tool_call_id"]))
                insert_position = j + 1
            else:
                break
        missing_ids = [tid for tid in tool_call_ids if tid not in responded_ids]
        if missing_ids:
            missing_responses = [
                {"role": "tool", "tool_call_id": tool_id, "content": "[No response received]"}
                for tool_id in missing_ids
            ]
            for offset, response in enumerate(missing_responses):
                messages.insert(insert_position + offset, response)
            i = insert_position + len(missing_responses) - 1
        i += 1

from janus.canonical.models import Message, Role, TextPart, ToolResult, ToolUse
from janus.canonical.tool_calls import (
    TOOL_ID_PATTERN,
    ensure_tool_call_ids,
    fix_missing_tool_responses,
    fix_missing_tool_responses_openai,
    inject_reasoning_content_openai,
    prepare_tool_messages,
)


def test_ensure_tool_call_ids_sanitizes_invalid_id() -> None:
    messages = [
        Message(
            role=Role.ASSISTANT,
            content=[ToolUse(id="bad id!", name="read", input={})],
        ),
    ]
    result = ensure_tool_call_ids(messages)
    tool_use = result[0].content[0]
    assert isinstance(tool_use, ToolUse)
    assert TOOL_ID_PATTERN.match(tool_use.id)


def test_fix_missing_tool_responses_inserts_empty_tool_message() -> None:
    messages = [
        Message(
            role=Role.ASSISTANT,
            content=[ToolUse(id="c1", name="read", input={})],
        ),
        Message(role=Role.USER, content=[TextPart(text="continue")]),
    ]
    result = fix_missing_tool_responses(messages)
    assert len(result) == 3
    assert result[1].role == Role.TOOL
    assert isinstance(result[1].content[0], ToolResult)
    assert result[1].content[0].tool_use_id == "c1"
    assert result[1].content[0].content == ""


def test_fix_missing_tool_responses_skips_when_next_has_tool_result() -> None:
    messages = [
        Message(
            role=Role.ASSISTANT,
            content=[ToolUse(id="c1", name="read", input={})],
        ),
        Message(
            role=Role.USER,
            content=[ToolResult(tool_use_id="c1", content="ok")],
        ),
    ]
    result = fix_missing_tool_responses(messages)
    assert len(result) == 2


def test_prepare_tool_messages_runs_sanitize_then_fill() -> None:
    messages = [
        Message(
            role=Role.ASSISTANT,
            content=[ToolUse(id="bad id", name="read", input={})],
        ),
        Message(role=Role.USER, content=[TextPart(text="next")]),
    ]
    result = prepare_tool_messages(messages)
    assert len(result) == 3
    assert result[1].role == Role.TOOL


def test_inject_reasoning_content_openai_for_deepseek() -> None:
    messages: list[dict[str, object]] = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
    ]
    inject_reasoning_content_openai(messages, "deepseek-v4-pro")
    assert messages[0]["reasoning_content"] == " "


def test_inject_reasoning_content_openai_skips_non_deepseek() -> None:
    messages: list[dict[str, object]] = [{"role": "assistant", "content": "hi"}]
    inject_reasoning_content_openai(messages, "gpt-4")
    assert "reasoning_content" not in messages[0]


def test_fix_missing_tool_responses_openai_inserts_placeholder() -> None:
    messages: list[dict[str, object]] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "g", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "done"},
    ]
    fix_missing_tool_responses_openai(messages)
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[1]["tool_call_id"] == "b"
    assert tool_msgs[1]["content"] == "[No response received]"

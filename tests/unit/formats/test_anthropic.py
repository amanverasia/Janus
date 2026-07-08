import json
from pathlib import Path

from janus.canonical.models import (
    CanonicalRequest,
    CanonicalResponse,
    Message,
    Reasoning,
    Role,
    SystemBlock,
    TextPart,
    ToolChoiceRequired,
    ToolChoiceSpecific,
    ToolResult,
    ToolUse,
    Usage,
)
from janus.formats.anthropic import AnthropicAdapter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_parse_message_request():
    raw = json.loads((FIXTURES / "anthropic_message_request.json").read_text())
    req = AnthropicAdapter().parse_request(raw)
    assert len(req.system) == 1
    assert req.system[0].text == "You are helpful."
    assert req.messages[0].role == Role.USER
    # content "Hello" should be a TextPart
    assert isinstance(req.messages[0].content[0], TextPart)
    assert req.messages[0].content[0].text == "Hello"
    # assistant message has tool_use
    assert isinstance(req.messages[1].content[0], ToolUse)
    assert req.messages[1].content[0].id == "t1"
    assert req.messages[1].content[0].name == "read"
    # user message has tool_result
    assert isinstance(req.messages[2].content[0], ToolResult)
    assert req.messages[2].content[0].tool_use_id == "t1"


def test_parse_tool_result_array_content() -> None:
    req = AnthropicAdapter().parse_request(
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [
                                {"type": "text", "text": "line1"},
                                {"type": "text", "text": "line2"},
                            ],
                        }
                    ],
                }
            ],
        }
    )
    tool_result = req.messages[0].content[0]
    assert isinstance(tool_result, ToolResult)
    assert tool_result.content == "line1\nline2"


def test_build_upstream_request():
    req = CanonicalRequest(
        model="claude-sonnet-4-20250514",
        system=[SystemBlock(type="text", text="Be concise")],
        messages=[Message(role=Role.USER, content=[TextPart(type="text", text="hi")])],
        max_tokens=1024,
    )
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert payload["system"][0]["text"] == "Be concise"
    assert payload["messages"][0]["content"][0]["text"] == "hi"
    assert payload["max_tokens"] == 1024


def test_parse_anthropic_stream():
    raw = (FIXTURES / "anthropic_stream.txt").read_text()
    parser = AnthropicAdapter().stream_parser()
    all_events = []
    for line in raw.split("\n"):
        if line.startswith("data: "):
            all_events.extend(parser.feed(line[6:]))
    all_events.extend(parser.finish())
    event_types = [e.type for e in all_events]
    assert "message_start" in event_types
    assert "text_block_start" in event_types
    assert "text_delta" in event_types
    assert "block_stop" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types


def test_emit_response():
    adapter = AnthropicAdapter()
    resp = CanonicalResponse(
        model="claude-sonnet-4-20250514",
        content=[TextPart(type="text", text="Hello!")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=2),
    )
    out = adapter.emit_response(resp)
    assert out["type"] == "message"
    assert out["content"][0]["text"] == "Hello!"
    assert out["stop_reason"] == "end_turn"


def test_stream_parses_thinking_and_signature():
    raw = (FIXTURES / "anthropic_thinking_stream.txt").read_text()
    parser = AnthropicAdapter().stream_parser()
    events = []
    for line in raw.split("\n"):
        if line.startswith("data: "):
            events.extend(parser.feed(line[6:]))
    types = [e.type for e in events]
    assert "reasoning_block_start" in types
    assert "reasoning_delta" in types
    sig_events = [e for e in events if e.type == "reasoning_delta" and e.signature]
    assert sig_events and sig_events[0].signature == "sigABC"


def test_emitter_serializes_reasoning_block():
    from janus.canonical.events import ReasoningBlockStart, ReasoningDelta

    emitter = AnthropicAdapter().stream_emitter()
    out = b"".join(emitter.feed(ReasoningBlockStart(index=0)))
    out += b"".join(emitter.feed(ReasoningDelta(index=0, text="hmm")))
    assert b"thinking" in out
    assert b"hmm" in out


def test_parse_thinking_and_tool_choice_request():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "thinking": {"type": "enabled", "budget_tokens": 2000},
        "tool_choice": {"type": "tool", "name": "read"},
        "messages": [{"role": "user", "content": "hi"}],
    }
    req = AnthropicAdapter().parse_request(raw)
    assert req.thinking is not None
    assert req.thinking["type"] == "enabled"
    assert isinstance(req.tool_choice, ToolChoiceSpecific)
    assert req.tool_choice.name == "read"


def test_parse_thinking_block_in_assistant_message():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "let me think", "signature": "sig1"},
                    {"type": "text", "text": "answer"},
                ],
            }
        ],
    }
    req = AnthropicAdapter().parse_request(raw)
    parts = req.messages[0].content
    assert isinstance(parts[0], Reasoning)
    assert parts[0].text == "let me think"
    assert parts[0].signature == "sig1"


def test_build_request_emits_thinking_and_tool_choice():
    req = CanonicalRequest(
        model="claude-sonnet-4-20250514",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        max_tokens=1024,
        thinking={"type": "enabled", "budget_tokens": "2000"},
        tool_choice=ToolChoiceRequired(),
    )
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    assert payload["thinking"]["type"] == "enabled"
    assert payload["tool_choice"]["type"] == "any"


def test_build_request_emits_reasoning_block():
    req = CanonicalRequest(
        model="claude-sonnet-4-20250514",
        messages=[
            Message(
                role=Role.ASSISTANT,
                content=[Reasoning(text="thoughts", signature="sig9"), TextPart(text="hi")],
            )
        ],
        max_tokens=1024,
    )
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    blocks = payload["messages"][0]["content"]
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["thinking"] == "thoughts"
    assert blocks[0]["signature"] == "sig9"


def test_parse_response_preserves_thinking():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "content": [
            {"type": "thinking", "thinking": "reasoned", "signature": "s"},
            {"type": "text", "text": "done"},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    resp = AnthropicAdapter().parse_upstream_response(raw)
    assert isinstance(resp.content[0], Reasoning)
    assert resp.content[0].text == "reasoned"
    assert isinstance(resp.content[1], TextPart)


def test_tool_result_is_error_roundtrip():
    raw = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "boom",
                        "is_error": True,
                    }
                ],
            }
        ],
    }
    req = AnthropicAdapter().parse_request(raw)
    tr = req.messages[0].content[0]
    assert isinstance(tr, ToolResult)
    assert tr.is_error is True
    payload = AnthropicAdapter().build_upstream_request(req, "claude-sonnet-4-20250514")
    assert payload["messages"][0]["content"][0]["is_error"] is True

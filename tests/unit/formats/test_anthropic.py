import json
from pathlib import Path

from janus.canonical.models import (
    CanonicalRequest,
    CanonicalResponse,
    Message,
    Role,
    SystemBlock,
    TextPart,
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

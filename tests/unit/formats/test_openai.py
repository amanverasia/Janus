import json
from pathlib import Path

from janus.canonical.events import (
    BlockStop,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextBlockStart,
    TextDelta,
)
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
from janus.formats.openai import OpenAIAdapter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def test_parse_simple_chat_request():
    raw = json.loads((FIXTURES / "openai_chat_request.json").read_text())
    req = OpenAIAdapter().parse_request(raw)
    assert req.model == "gpt-4"
    assert len(req.system) == 1
    assert req.system[0].text == "You are helpful."
    assert req.messages[0].content[0].text == "Hello"  # type: ignore[union-attr]
    assert req.tools[0].function.name == "read"


def test_build_upstream_request():
    req = CanonicalRequest(
        model="gpt-4",
        system=[SystemBlock(type="text", text="Be concise")],
        messages=[Message(role=Role.USER, content="hi")],
        max_tokens=100,
    )
    adapter = OpenAIAdapter()
    payload = adapter.build_upstream_request(req, "gpt-4")
    assert payload["model"] == "gpt-4"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "Be concise"
    assert payload["messages"][1]["role"] == "user"
    assert payload["messages"][1]["content"] == "hi"


def test_parse_upstream_response():
    raw = json.loads((FIXTURES / "openai_nonstream_response.json").read_text())
    resp = OpenAIAdapter().parse_upstream_response(raw)
    assert resp.content[0].text == "Hello!"  # type: ignore[union-attr]
    assert resp.stop_reason == "end_turn"
    assert resp.usage.input_tokens == 10


def test_emit_response():
    adapter = OpenAIAdapter()
    resp = CanonicalResponse(
        model="gpt-4",
        content=[TextPart(type="text", text="Hello!")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=2),
    )
    out = adapter.emit_response(resp)
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"]["content"] == "Hello!"
    assert out["choices"][0]["finish_reason"] == "stop"


def test_parse_upstream_stream():
    raw = (FIXTURES / "openai_stream.txt").read_text()
    parser = OpenAIAdapter().stream_parser()
    all_events = []
    for line in raw.split("\n"):
        if not line.strip():
            continue
        all_events.extend(parser.feed(line))
    all_events.extend(parser.finish())

    event_types = [e.type for e in all_events]
    assert "message_start" in event_types
    assert "text_block_start" in event_types
    assert "text_delta" in event_types
    assert "block_stop" in event_types
    assert "message_delta" in event_types
    assert "message_stop" in event_types

    text = "".join(e.text for e in all_events if hasattr(e, "text"))
    assert "Hello" in text


def test_emit_stream():
    emitter = OpenAIAdapter().stream_emitter()
    events = [
        MessageStart(model="gpt-4"),
        TextBlockStart(index=0),
        TextDelta(index=0, text="Hi"),
        BlockStop(index=0),
        MessageDelta(stop_reason="end_turn"),
        MessageStop(),
    ]
    chunks = []
    for ev in events:
        chunks.extend(emitter.feed(ev))
    chunks.extend(emitter.finish())

    output = b"".join(chunks).decode()
    assert "chat.completion.chunk" in output
    assert "Hi" in output
    assert "[DONE]" in output


def test_tool_use_in_parse():
    """OpenAI assistant message with tool_calls should parse to ToolUse parts."""
    raw = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "read file"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "x.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
        ],
    }
    req = OpenAIAdapter().parse_request(raw)
    assistant_msg = req.messages[1]
    assert assistant_msg.role == Role.ASSISTANT
    tool_use = assistant_msg.content[0]
    assert isinstance(tool_use, ToolUse)
    assert tool_use.id == "call_1"
    assert tool_use.name == "read"
    assert tool_use.input == {"path": "x.py"}

    tool_msg = req.messages[2]
    assert tool_msg.role == Role.TOOL
    assert isinstance(tool_msg.content[0], ToolResult)
    assert tool_msg.content[0].tool_use_id == "call_1"

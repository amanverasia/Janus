import json

from janus.canonical.events import (
    BlockStop,
    InputJsonDelta,
    MessageDelta,
    MessageStart,
    MessageStop,
    ReasoningDelta,
    TextBlockStart,
    TextDelta,
    ToolUseBlockStart,
)
from janus.canonical.models import (
    CanonicalResponse,
    ImagePart,
    TextPart,
    ToolResult,
    ToolUse,
    Usage,
)
from janus.formats.ollama import OllamaAdapter

adapter = OllamaAdapter()


# ---- request parsing ----


def test_parse_request_defaults_to_streaming():
    req = adapter.parse_request(
        {"model": "test/m1", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert req.stream is True
    req = adapter.parse_request({"model": "test/m1", "messages": [], "stream": False})
    assert req.stream is False


def test_parse_request_system_and_options():
    req = adapter.parse_request(
        {
            "model": "test/m1",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
            "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 128, "stop": "END"},
        }
    )
    assert req.system[0].text == "be brief"
    assert req.temperature == 0.3
    assert req.top_p == 0.9
    assert req.max_tokens == 128
    assert req.stop == ["END"]


def test_parse_request_tool_round_trip_assigns_ids():
    req = adapter.parse_request(
        {
            "model": "test/m1",
            "messages": [
                {"role": "user", "content": "list files"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "ls", "arguments": {"path": "."}}}],
                },
                {"role": "tool", "content": "a.txt"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "ls", "parameters": {"type": "object"}},
                }
            ],
        }
    )
    tool_use = req.messages[1].content[0]
    assert isinstance(tool_use, ToolUse)
    assert tool_use.input == {"path": "."}
    tool_result = req.messages[2].content[0]
    assert isinstance(tool_result, ToolResult)
    assert tool_result.tool_use_id == tool_use.id
    assert tool_result.content == "a.txt"
    assert req.tools[0].function.name == "ls"


def test_parse_request_images():
    req = adapter.parse_request(
        {
            "model": "test/m1",
            "messages": [{"role": "user", "content": "what is this", "images": ["aGVsbG8="]}],
        }
    )
    image = req.messages[0].content[1]
    assert isinstance(image, ImagePart)
    assert image.source.type == "base64"
    assert image.source.data == "aGVsbG8="


# ---- upstream response parsing ----


def test_parse_upstream_response():
    resp = adapter.parse_upstream_response(
        {
            "model": "llama3",
            "message": {"role": "assistant", "content": "hello", "thinking": "hmm"},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 7,
        }
    )
    assert resp.content[0].text == "hello"
    assert resp.reasoning_content == "hmm"
    assert resp.stop_reason == "end_turn"
    assert resp.usage.input_tokens == 12
    assert resp.usage.output_tokens == 7


def test_parse_upstream_response_tool_calls():
    resp = adapter.parse_upstream_response(
        {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "ls", "arguments": {"path": "."}}}],
            },
            "done": True,
        }
    )
    tool_use = resp.content[0]
    assert isinstance(tool_use, ToolUse)
    assert tool_use.name == "ls"
    assert tool_use.id
    assert resp.stop_reason == "tool_use"


# ---- response emitting ----


def test_emit_response():
    resp = CanonicalResponse(
        model="test/m1",
        content=[TextPart(text="done"), ToolUse(id="c1", name="ls", input={"path": "."})],
        stop_reason="tool_use",
        usage=Usage(input_tokens=5, output_tokens=2),
    )
    out = adapter.emit_response(resp)
    assert out["done"] is True
    assert out["done_reason"] == "tool_calls"
    assert out["message"]["content"] == "done"
    assert out["message"]["tool_calls"][0]["function"] == {
        "name": "ls",
        "arguments": {"path": "."},
    }
    assert out["prompt_eval_count"] == 5
    assert out["eval_count"] == 2


def test_emit_response_max_tokens():
    resp = CanonicalResponse(model="m", content=[TextPart(text="x")], stop_reason="max_tokens")
    assert adapter.emit_response(resp)["done_reason"] == "length"


# ---- upstream request building ----


def test_build_upstream_request():
    req = adapter.parse_request(
        {
            "model": "test/m1",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hi"},
            ],
            "options": {"num_predict": 64},
            "stream": False,
        }
    )
    payload = adapter.build_upstream_request(req, "m1")
    assert payload["model"] == "m1"
    assert payload["stream"] is False
    assert payload["options"] == {"num_predict": 64}
    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}


# ---- streaming emitter ----


def _decode_ndjson(chunks: list[bytes]) -> list[dict]:
    lines = b"".join(chunks).decode().strip().split("\n")
    return [json.loads(line) for line in lines if line]


def test_stream_emitter_text_sequence():
    emitter = adapter.stream_emitter()
    chunks: list[bytes] = []
    chunks += emitter.feed(MessageStart(model="test/m1"))
    chunks += emitter.feed(TextBlockStart(index=0))
    chunks += emitter.feed(TextDelta(index=0, text="hel"))
    chunks += emitter.feed(TextDelta(index=0, text="lo"))
    chunks += emitter.feed(BlockStop(index=0))
    chunks += emitter.feed(
        MessageDelta(stop_reason="end_turn", usage=Usage(input_tokens=4, output_tokens=2))
    )
    chunks += emitter.feed(MessageStop())
    chunks += emitter.finish()

    events = _decode_ndjson(chunks)
    assert events[0]["message"]["content"] == "hel"
    assert events[1]["message"]["content"] == "lo"
    final = events[-1]
    assert final["done"] is True
    assert final["done_reason"] == "stop"
    assert final["prompt_eval_count"] == 4
    assert final["eval_count"] == 2
    assert all(not e["done"] for e in events[:-1])


def test_stream_emitter_tool_call():
    emitter = adapter.stream_emitter()
    chunks: list[bytes] = []
    chunks += emitter.feed(MessageStart(model="test/m1"))
    chunks += emitter.feed(ToolUseBlockStart(index=0, id="c1", name="ls"))
    chunks += emitter.feed(InputJsonDelta(index=0, partial_json='{"path"'))
    chunks += emitter.feed(InputJsonDelta(index=0, partial_json=': "."}'))
    chunks += emitter.feed(BlockStop(index=0))
    chunks += emitter.feed(MessageDelta(stop_reason="tool_use"))
    chunks += emitter.feed(MessageStop())

    events = _decode_ndjson(chunks)
    tool_event = events[0]
    assert tool_event["message"]["tool_calls"][0]["function"] == {
        "name": "ls",
        "arguments": {"path": "."},
    }
    assert events[-1]["done_reason"] == "tool_calls"


def test_stream_emitter_finish_idempotent():
    emitter = adapter.stream_emitter()
    emitter.feed(MessageStart(model="m"))
    assert emitter.finish()
    assert emitter.finish() == []


# ---- streaming parser ----


def test_stream_parser_round_trip():
    parser = adapter.stream_parser()
    events = []
    lines = [
        '{"model":"llama3","message":{"role":"assistant","content":"hel"},"done":false}',
        '{"model":"llama3","message":{"role":"assistant","content":"lo"},"done":false}',
        (
            '{"model":"llama3","message":{"role":"assistant","content":""},"done":true,'
            '"done_reason":"stop","prompt_eval_count":9,"eval_count":3}'
        ),
    ]
    for line in lines:
        events.extend(parser.feed(line))
    events.extend(parser.finish())

    assert isinstance(events[0], MessageStart)
    assert events[0].model == "llama3"
    assert isinstance(events[1], TextBlockStart)
    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert [d.text for d in deltas] == ["hel", "lo"]
    md = next(e for e in events if isinstance(e, MessageDelta))
    assert md.stop_reason == "end_turn"
    assert md.usage is not None and md.usage.input_tokens == 9
    assert isinstance(events[-1], MessageStop)


def test_stream_parser_tool_calls_and_thinking():
    parser = adapter.stream_parser()
    events = []
    lines = [
        '{"model":"llama3","message":{"role":"assistant","thinking":"hmm"},"done":false}',
        (
            '{"model":"llama3","message":{"role":"assistant","content":"",'
            '"tool_calls":[{"function":{"name":"ls","arguments":{"path":"."}}}]},'
            '"done":true,"done_reason":"stop"}'
        ),
    ]
    for line in lines:
        events.extend(parser.feed(line))

    assert any(isinstance(e, ReasoningDelta) and e.text == "hmm" for e in events)
    start = next(e for e in events if isinstance(e, ToolUseBlockStart))
    assert start.name == "ls"
    delta = next(e for e in events if isinstance(e, InputJsonDelta))
    assert json.loads(delta.partial_json) == {"path": "."}
    md = next(e for e in events if isinstance(e, MessageDelta))
    assert md.stop_reason == "tool_use"


def test_reasoning_part_renders_as_text_in_ollama():
    from janus.canonical.models import CanonicalRequest, Message, Reasoning, Role, TextPart
    from janus.formats.ollama import OllamaAdapter

    req = CanonicalRequest(
        model="llama3",
        messages=[
            Message(role=Role.ASSISTANT, content=[Reasoning(text="pondering"), TextPart(text="hi")])
        ],
    )
    payload = OllamaAdapter().build_upstream_request(req, "llama3")
    assert payload is not None
    # assistant message content should include the visible text, not crash on Reasoning
    assistant = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert assistant
    assert "hi" in assistant[0]["content"]

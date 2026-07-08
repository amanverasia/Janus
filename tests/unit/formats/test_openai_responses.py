import json

from janus.canonical.events import (
    BlockStop,
    InputJsonDelta,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextBlockStart,
    TextDelta,
    ToolUseBlockStart,
)
from janus.canonical.models import (
    CanonicalRequest,
    CanonicalResponse,
    Message,
    Role,
    SystemBlock,
    TextPart,
    Tool,
    ToolChoiceSpecific,
    ToolFunction,
    ToolResult,
    ToolUse,
    Usage,
)
from janus.formats.openai_responses import OpenAIResponsesAdapter

adapter = OpenAIResponsesAdapter()


# ---- request parsing ----


def test_parse_request_string_input():
    req = adapter.parse_request({"model": "gpt-5", "input": "hello"})
    assert req.model == "gpt-5"
    assert len(req.messages) == 1
    assert req.messages[0].role == Role.USER
    assert req.messages[0].content[0].text == "hello"


def test_parse_request_instructions_and_system_items():
    req = adapter.parse_request(
        {
            "model": "gpt-5",
            "instructions": "be brief",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "extra rules"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hi"}],
                },
            ],
        }
    )
    assert [b.text for b in req.system] == ["be brief", "extra rules"]
    assert len(req.messages) == 1
    assert req.messages[0].role == Role.USER


def test_parse_request_tool_call_round_trip_items():
    req = adapter.parse_request(
        {
            "model": "gpt-5",
            "input": [
                {"type": "message", "role": "user", "content": "list files"},
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "ls",
                    "arguments": '{"path": "."}',
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "a.txt"},
            ],
        }
    )
    assert req.messages[1].role == Role.ASSISTANT
    tool_use = req.messages[1].content[0]
    assert isinstance(tool_use, ToolUse)
    assert tool_use.id == "call_1"
    assert tool_use.input == {"path": "."}
    tool_result = req.messages[2].content[0]
    assert isinstance(tool_result, ToolResult)
    assert tool_result.tool_use_id == "call_1"
    assert tool_result.content == "a.txt"


def test_parse_request_flat_tools_and_params():
    req = adapter.parse_request(
        {
            "model": "gpt-5",
            "input": "hi",
            "tools": [
                {
                    "type": "function",
                    "name": "ls",
                    "description": "list",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
            "tool_choice": {"type": "function", "name": "ls"},
            "max_output_tokens": 512,
            "temperature": 0.5,
            "reasoning": {"effort": "high"},
            "stream": True,
        }
    )
    assert req.tools[0].function.name == "ls"
    assert isinstance(req.tool_choice, ToolChoiceSpecific)
    assert req.tool_choice.name == "ls"
    assert req.max_tokens == 512
    assert req.temperature == 0.5
    assert req.reasoning_effort == "high"
    assert req.stream is True


def test_parse_request_skips_reasoning_items():
    req = adapter.parse_request(
        {
            "model": "gpt-5",
            "input": [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {"type": "message", "role": "user", "content": "hi"},
            ],
        }
    )
    assert len(req.messages) == 1


# ---- upstream request building ----


def test_build_upstream_request():
    req = CanonicalRequest(
        model="test/m1",
        system=[SystemBlock(text="be brief")],
        messages=[
            Message(role=Role.USER, content=[TextPart(text="list files")]),
            Message(
                role=Role.ASSISTANT,
                content=[ToolUse(id="call_1", name="ls", input={"path": "."})],
            ),
            Message(
                role=Role.TOOL,
                content=[ToolResult(tool_use_id="call_1", content="a.txt")],
            ),
        ],
        tools=[Tool(function=ToolFunction(name="ls", parameters={}))],
        max_tokens=256,
        stream=True,
    )
    payload = adapter.build_upstream_request(req, "m1")
    assert payload["model"] == "m1"
    assert payload["instructions"] == "be brief"
    assert payload["max_output_tokens"] == 256
    assert payload["stream"] is True
    assert payload["store"] is False
    assert payload["tools"][0] == {
        "type": "function",
        "name": "ls",
        "description": None,
        "parameters": {},
    }
    types = [item["type"] for item in payload["input"]]
    assert types == ["message", "function_call", "function_call_output"]
    assert payload["input"][1]["call_id"] == "call_1"
    assert json.loads(payload["input"][1]["arguments"]) == {"path": "."}
    assert payload["input"][2]["output"] == "a.txt"


# ---- upstream response parsing ----


def test_parse_upstream_response():
    resp = adapter.parse_upstream_response(
        {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "model": "m1",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_9",
                    "name": "grep",
                    "arguments": '{"q": "x"}',
                },
            ],
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens": 5,
                "total_tokens": 15,
            },
        }
    )
    assert resp.content[0].text == "hello"
    tool_use = resp.content[1]
    assert isinstance(tool_use, ToolUse)
    assert tool_use.id == "call_9"
    assert tool_use.input == {"q": "x"}
    assert resp.stop_reason == "tool_use"
    assert resp.usage.input_tokens == 10
    assert resp.usage.cache_read_input_tokens == 4


def test_parse_upstream_response_incomplete():
    resp = adapter.parse_upstream_response(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "model": "m1",
            "output": [],
        }
    )
    assert resp.stop_reason == "max_tokens"


# ---- response emitting ----


def test_emit_response_text_and_tools():
    resp = CanonicalResponse(
        model="m1",
        content=[
            TextPart(text="done"),
            ToolUse(id="call_1", name="ls", input={"path": "."}),
        ],
        stop_reason="tool_use",
        usage=Usage(input_tokens=7, output_tokens=3),
    )
    out = adapter.emit_response(resp)
    assert out["object"] == "response"
    assert out["status"] == "completed"
    msg = next(i for i in out["output"] if i["type"] == "message")
    assert msg["content"][0]["text"] == "done"
    fc = next(i for i in out["output"] if i["type"] == "function_call")
    assert fc["call_id"] == "call_1"
    assert json.loads(fc["arguments"]) == {"path": "."}
    assert out["usage"]["total_tokens"] == 10


def test_emit_response_incomplete():
    resp = CanonicalResponse(model="m1", content=[TextPart(text="x")], stop_reason="max_tokens")
    out = adapter.emit_response(resp)
    assert out["status"] == "incomplete"
    assert out["incomplete_details"] == {"reason": "max_output_tokens"}


# ---- streaming emitter ----


def _decode_events(chunks: list[bytes]) -> list[dict]:
    events = []
    for chunk in chunks:
        for line in chunk.decode().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_stream_emitter_text_sequence():
    emitter = adapter.stream_emitter()
    chunks: list[bytes] = []
    chunks += emitter.feed(MessageStart(model="m1"))
    chunks += emitter.feed(TextBlockStart(index=0))
    chunks += emitter.feed(TextDelta(index=0, text="hel"))
    chunks += emitter.feed(TextDelta(index=0, text="lo"))
    chunks += emitter.feed(BlockStop(index=0))
    chunks += emitter.feed(
        MessageDelta(stop_reason="end_turn", usage=Usage(input_tokens=5, output_tokens=2))
    )
    chunks += emitter.feed(MessageStop())
    chunks += emitter.finish()

    events = _decode_events(chunks)
    types = [e["type"] for e in events]
    assert types[0] == "response.created"
    assert "response.output_item.added" in types
    assert types.count("response.output_text.delta") == 2
    assert "response.output_text.done" in types
    assert types[-1] == "response.completed"
    final = events[-1]["response"]
    assert final["status"] == "completed"
    assert final["output"][0]["content"][0]["text"] == "hello"
    assert final["usage"]["input_tokens"] == 5
    assert all("sequence_number" in e for e in events)


def test_stream_emitter_tool_call_sequence():
    emitter = adapter.stream_emitter()
    chunks: list[bytes] = []
    chunks += emitter.feed(MessageStart(model="m1"))
    chunks += emitter.feed(ToolUseBlockStart(index=0, id="call_1", name="ls"))
    chunks += emitter.feed(InputJsonDelta(index=0, partial_json='{"path"'))
    chunks += emitter.feed(InputJsonDelta(index=0, partial_json=': "."}'))
    chunks += emitter.feed(BlockStop(index=0))
    chunks += emitter.feed(MessageDelta(stop_reason="tool_use"))
    chunks += emitter.feed(MessageStop())

    events = _decode_events(chunks)
    types = [e["type"] for e in events]
    assert "response.function_call_arguments.delta" in types
    done = next(e for e in events if e["type"] == "response.function_call_arguments.done")
    assert done["arguments"] == '{"path": "."}'
    final = events[-1]["response"]
    assert final["output"][0]["type"] == "function_call"
    assert final["output"][0]["call_id"] == "call_1"


def test_stream_emitter_finish_is_idempotent():
    emitter = adapter.stream_emitter()
    emitter.feed(MessageStart(model="m1"))
    first = emitter.finish()
    assert first
    assert emitter.finish() == []


# ---- streaming parser ----


def test_stream_parser_round_trip():
    parser = adapter.stream_parser()
    events = []
    lines = [
        'data: {"type":"response.created","response":{"model":"m1"}}',
        (
            'data: {"type":"response.output_item.added","output_index":0,'
            '"item":{"type":"message","role":"assistant"}}'
        ),
        (
            'data: {"type":"response.content_part.added","output_index":0,'
            '"part":{"type":"output_text","text":""}}'
        ),
        'data: {"type":"response.output_text.delta","output_index":0,"delta":"hi"}',
        'data: {"type":"response.output_item.done","output_index":0,"item":{}}',
        (
            'data: {"type":"response.completed","response":{"status":"completed",'
            '"output":[],"usage":{"input_tokens":3,"output_tokens":1}}}'
        ),
    ]
    for line in lines:
        events.extend(parser.feed(line))
    events.extend(parser.finish())

    assert isinstance(events[0], MessageStart)
    assert events[0].model == "m1"
    assert isinstance(events[1], TextBlockStart)
    assert isinstance(events[2], TextDelta)
    assert events[2].text == "hi"
    assert isinstance(events[3], BlockStop)
    assert isinstance(events[4], MessageDelta)
    assert events[4].usage is not None
    assert events[4].usage.input_tokens == 3
    assert isinstance(events[5], MessageStop)


def test_stream_parser_function_call():
    parser = adapter.stream_parser()
    events = []
    lines = [
        'data: {"type":"response.created","response":{"model":"m1"}}',
        (
            'data: {"type":"response.output_item.added","output_index":0,'
            '"item":{"type":"function_call","call_id":"call_1","name":"ls"}}'
        ),
        ('data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{}"}'),
        'data: {"type":"response.output_item.done","output_index":0,"item":{}}',
        (
            'data: {"type":"response.completed","response":{"status":"completed",'
            '"output":[{"type":"function_call"}]}}'
        ),
    ]
    for line in lines:
        events.extend(parser.feed(line))

    start = next(e for e in events if isinstance(e, ToolUseBlockStart))
    assert start.id == "call_1"
    assert start.name == "ls"
    delta = next(e for e in events if isinstance(e, InputJsonDelta))
    assert delta.partial_json == "{}"
    md = next(e for e in events if isinstance(e, MessageDelta))
    assert md.stop_reason == "tool_use"


def test_reasoning_part_does_not_crash_responses_build():
    from janus.canonical.models import CanonicalRequest, Message, Reasoning, Role, TextPart
    from janus.formats.openai_responses import OpenAIResponsesAdapter

    req = CanonicalRequest(
        model="gpt-5",
        messages=[Message(role=Role.ASSISTANT, content=[Reasoning(text="t"), TextPart(text="hi")])],
    )
    payload = OpenAIResponsesAdapter().build_upstream_request(req, "gpt-5")
    assert payload is not None

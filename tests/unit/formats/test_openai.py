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
    ToolChoiceNone,
    ToolChoiceRequired,
    ToolChoiceSpecific,
    ToolResult,
    ToolUse,
    Usage,
)
from janus.formats.openai import OpenAIAdapter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _oai_chunk(**kwargs: object) -> str:
    base: dict[str, object] = {"id": "r1", "object": "chat.completion.chunk"}
    base.update(kwargs)
    return json.dumps(base, separators=(",", ":"))


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


def test_build_upstream_request_stream_injects_include_usage():
    req = CanonicalRequest(
        model="gpt-4",
        system=[SystemBlock(type="text", text="Be concise")],
        messages=[Message(role=Role.USER, content="hi")],
        stream=True,
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4")
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


def test_build_upstream_request_nonstream_no_stream_options():
    req = CanonicalRequest(
        model="gpt-4",
        messages=[Message(role=Role.USER, content="hi")],
        stream=False,
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4")
    assert "stream" not in payload
    assert "stream_options" not in payload


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


def test_parse_upstream_stream_with_usage():
    """OpenAI parser should extract usage from the final chunk when include_usage is set."""
    parser = OpenAIAdapter().stream_parser()
    lines = [
        '{"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
        '{"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}',
        '{"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
        '{"id":"r1","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":42,"completion_tokens":7,"total_tokens":49}}',
        "[DONE]",
    ]
    all_events: list = []
    for line in lines:
        all_events.extend(parser.feed(line))
    all_events.extend(parser.finish())

    usage_deltas = [e for e in all_events if isinstance(e, MessageDelta) and e.usage is not None]
    assert len(usage_deltas) == 1
    assert usage_deltas[0].usage.input_tokens == 42
    assert usage_deltas[0].usage.output_tokens == 7


def test_parse_openrouter_duplicate_finish_empty_content():
    """OpenRouter Claude streams often re-send empty content + finish_reason.

    That used to reopen a second Anthropic text block after stop_reason=end_turn,
    which breaks strict Anthropic clients intermittently.
    """
    parser = OpenAIAdapter().stream_parser()
    lines = [
        (
            '{"id":"g1","object":"chat.completion.chunk","choices":[{"index":0,'
            '"delta":{"content":"1","role":"assistant"},"finish_reason":null}]}'
        ),
        (
            '{"id":"g1","object":"chat.completion.chunk","choices":[{"index":0,'
            '"delta":{"content":"","role":"assistant"},"finish_reason":"stop"}]}'
        ),
        (
            '{"id":"g1","object":"chat.completion.chunk","choices":[{"index":0,'
            '"delta":{"content":"","role":"assistant"},"finish_reason":"stop"}]}'
        ),
        (
            '{"id":"g1","object":"chat.completion.chunk","choices":[],'
            '"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}'
        ),
        "[DONE]",
    ]
    all_events: list = []
    for line in lines:
        all_events.extend(parser.feed(line))
    all_events.extend(parser.finish())

    text_starts = [e for e in all_events if isinstance(e, TextBlockStart)]
    stop_reasons = [
        e.stop_reason for e in all_events if isinstance(e, MessageDelta) and e.stop_reason
    ]
    assert len(text_starts) == 1
    assert stop_reasons == ["end_turn"]
    usage_deltas = [e for e in all_events if isinstance(e, MessageDelta) and e.usage is not None]
    assert len(usage_deltas) == 1
    assert usage_deltas[0].usage.output_tokens == 1

    # Round-trip through Anthropic emitter: no content block after message_delta stop.
    from janus.formats.anthropic import AnthropicAdapter

    emitter = AnthropicAdapter().stream_emitter()
    out = b"".join(chunk for ev in all_events for chunk in emitter.feed(ev))
    out += b"".join(emitter.finish())
    text = out.decode()
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    types = [e.get("type") for e in events]
    assert types.count("content_block_start") == 1
    stop_idx = types.index("message_delta")
    assert "content_block_start" not in types[stop_idx:]


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


def test_build_upstream_anthropic_style_tool_results():
    """Anthropic tool_result blocks in user messages must become OpenAI role=tool."""
    from janus.formats.anthropic import AnthropicAdapter

    raw = json.loads((FIXTURES / "anthropic_message_request.json").read_text())
    req = AnthropicAdapter().parse_request(raw)
    payload = OpenAIAdapter().build_upstream_request(req, "deepseek-v4-pro")

    tool_msgs = [m for m in payload["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "t1"
    assert tool_msgs[0]["content"] == "print('hello')"

    assistant_msgs = [m for m in payload["messages"] if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["tool_calls"][0]["id"] == "t1"


def test_build_upstream_mixed_tool_result_before_user_text() -> None:
    req = CanonicalRequest(
        model="deepseek-v4-pro",
        messages=[
            Message(
                role=Role.USER,
                content=[
                    TextPart(text="also"),
                    ToolResult(tool_use_id="t1", content="result"),
                ],
            ),
        ],
    )
    payload = OpenAIAdapter().build_upstream_request(req, "deepseek-v4-pro")
    roles = [m["role"] for m in payload["messages"]]
    assert roles.index("tool") < roles.index("user")


def test_build_upstream_inserts_missing_openai_tool_responses() -> None:
    req = CanonicalRequest(
        model="deepseek-v4-pro",
        messages=[
            Message(
                role=Role.ASSISTANT,
                content=[ToolUse(id="c1", name="read", input={})],
            ),
            Message(role=Role.USER, content=[TextPart(text="continue")]),
        ],
    )
    payload = OpenAIAdapter().build_upstream_request(req, "deepseek-v4-pro")
    tool_msgs = [m for m in payload["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert tool_msgs[0]["content"] == "[No response received]"


def test_parse_thinking_from_extra_body() -> None:
    raw = {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    req = OpenAIAdapter().parse_request(raw)
    assert req.thinking == {"type": "disabled"}


def test_parse_thinking_and_reasoning_effort_top_level() -> None:
    raw = {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }
    req = OpenAIAdapter().parse_request(raw)
    assert req.thinking == {"type": "enabled"}
    assert req.reasoning_effort == "max"


def test_build_upstream_passes_thinking_for_deepseek() -> None:
    req = CanonicalRequest(
        model="deepseek/deepseek-v4-pro",
        messages=[Message(role=Role.USER, content="hi")],
        thinking={"type": "enabled"},
        reasoning_effort="high",
    )
    payload = OpenAIAdapter().build_upstream_request(req, "deepseek-v4-pro")
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_parse_reasoning_effort_xhigh_from_extra_body() -> None:
    raw = {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "extra_body": {"reasoning_effort": "xhigh", "thinking": {"type": "enabled"}},
    }
    req = OpenAIAdapter().parse_request(raw)
    assert req.reasoning_effort == "xhigh"


def test_parse_reasoning_effort_max_from_output_config() -> None:
    raw = {
        "model": "deepseek/deepseek-v4-pro",
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"effort": "max"},
    }
    req = OpenAIAdapter().parse_request(raw)
    assert req.reasoning_effort == "max"


def test_build_upstream_passes_xhigh_reasoning_effort_for_deepseek() -> None:
    req = CanonicalRequest(
        model="deepseek/deepseek-v4-pro",
        messages=[Message(role=Role.USER, content="hi")],
        thinking={"type": "enabled"},
        reasoning_effort="xhigh",
    )
    payload = OpenAIAdapter().build_upstream_request(req, "deepseek-v4-pro")
    assert payload["reasoning_effort"] == "xhigh"


def test_build_upstream_skips_thinking_for_non_deepseek() -> None:
    req = CanonicalRequest(
        model="openai/gpt-4o",
        messages=[Message(role=Role.USER, content="hi")],
        thinking={"type": "enabled"},
        reasoning_effort="high",
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4o")
    assert "thinking" not in payload
    assert payload["reasoning_effort"] == "high"


def test_parse_upstream_stream_reasoning_content() -> None:
    parser = OpenAIAdapter().stream_parser()
    lines = [
        _oai_chunk(choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]),
        _oai_chunk(
            choices=[
                {
                    "index": 0,
                    "delta": {"reasoning_content": "Let me think"},
                    "finish_reason": None,
                }
            ]
        ),
        _oai_chunk(
            choices=[
                {
                    "index": 0,
                    "delta": {"reasoning_content": " about this."},
                    "finish_reason": None,
                }
            ]
        ),
        _oai_chunk(choices=[{"index": 0, "delta": {"content": "Answer"}, "finish_reason": None}]),
        _oai_chunk(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]),
        "[DONE]",
    ]
    from janus.canonical.events import ReasoningDelta

    all_events: list = []
    for line in lines:
        all_events.extend(parser.feed(line))
    all_events.extend(parser.finish())

    reasoning = "".join(e.text for e in all_events if isinstance(e, ReasoningDelta))
    assert reasoning == "Let me think about this."


def test_emit_stream_reasoning_content() -> None:
    from janus.canonical.events import ReasoningBlockStart, ReasoningDelta

    emitter = OpenAIAdapter().stream_emitter()
    events = [
        MessageStart(model="deepseek-v4-pro"),
        ReasoningBlockStart(index=0),
        ReasoningDelta(index=0, text="Thinking"),
        MessageStop(),
    ]
    chunks = []
    for ev in events:
        chunks.extend(emitter.feed(ev))
    output = b"".join(chunks).decode()
    assert "reasoning_content" in output
    assert "Thinking" in output


def test_parse_tool_choice_specific():
    req = OpenAIAdapter().parse_request(
        {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": {"type": "function", "function": {"name": "search"}},
        }
    )
    assert isinstance(req.tool_choice, ToolChoiceSpecific)
    assert req.tool_choice.name == "search"


def test_parse_tool_choice_required():
    req = OpenAIAdapter().parse_request(
        {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": "required",
        }
    )
    assert isinstance(req.tool_choice, ToolChoiceRequired)


def test_build_emits_tool_choice():
    req = CanonicalRequest(
        model="gpt-4o",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceSpecific(name="search"),
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4o")
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "search"}}


def test_build_emits_tool_choice_none():
    req = CanonicalRequest(
        model="gpt-4o",
        messages=[Message(role=Role.USER, content=[TextPart(text="hi")])],
        tool_choice=ToolChoiceNone(),
    )
    payload = OpenAIAdapter().build_upstream_request(req, "gpt-4o")
    assert payload["tool_choice"] == "none"

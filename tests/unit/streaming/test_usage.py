from __future__ import annotations

import json
import types

from janus.canonical.events import (
    InputJsonDelta,
    MessageStart,
    ReasoningDelta,
    TextDelta,
    ToolUseBlockStart,
)
from janus.formats.anthropic import AnthropicAdapter
from janus.formats.openai import OpenAIAdapter
from janus.streaming.usage import StreamUsageTracker


def _oai_chunk(**kwargs: object) -> str:
    base: dict[str, object] = {"id": "r1", "object": "chat.completion.chunk"}
    base.update(kwargs)
    return json.dumps(base, separators=(",", ":"))


def _oai_choice(delta: dict[str, object], finish: str | None = None) -> list[dict[str, object]]:
    return [{"index": 0, "delta": delta, "finish_reason": finish}]


def _ant(event: str, **kwargs: object) -> str:
    base: dict[str, object] = {"type": event}
    base.update(kwargs)
    return json.dumps(base, separators=(",", ":"))


def test_tracker_captures_usage_from_anthropic_stream():
    parser = AnthropicAdapter().stream_parser()
    tracker = StreamUsageTracker(parser)

    lines = [
        _ant(
            "message_start",
            message={
                "id": "m",
                "model": "claude",
                "usage": {"input_tokens": 25, "output_tokens": 0},
            },
        ),
        _ant("content_block_start", index=0, content_block={"type": "text", "text": ""}),
        _ant("content_block_delta", index=0, delta={"type": "text_delta", "text": "Hello"}),
        _ant("content_block_delta", index=0, delta={"type": "text_delta", "text": " world"}),
        _ant("content_block_stop", index=0),
        _ant("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 5}),
        _ant("message_stop"),
    ]
    for line in lines:
        tracker.feed(line)
    tracker.finish()

    usage = tracker.get_usage()
    assert usage.input_tokens == 25
    assert usage.output_tokens == 5


def test_tracker_captures_usage_from_openai_stream_with_include_usage():
    parser = OpenAIAdapter().stream_parser()
    tracker = StreamUsageTracker(parser)

    lines = [
        _oai_chunk(choices=_oai_choice({"role": "assistant"})),
        _oai_chunk(choices=_oai_choice({"content": "Hi there"})),
        _oai_chunk(choices=_oai_choice({}, finish="stop")),
        _oai_chunk(
            choices=[], usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        ),
        "[DONE]",
    ]
    for line in lines:
        tracker.feed(line)
    tracker.finish()

    usage = tracker.get_usage()
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50


def test_tracker_fallback_tiktoken_when_no_usage():
    parser = OpenAIAdapter().stream_parser()
    tracker = StreamUsageTracker(parser)

    lines = [
        _oai_chunk(choices=_oai_choice({"role": "assistant"})),
        _oai_chunk(choices=_oai_choice({"content": "Hello world this is a test"})),
        _oai_chunk(choices=_oai_choice({}, finish="stop")),
        "[DONE]",
    ]
    for line in lines:
        tracker.feed(line)
    tracker.finish()

    usage = tracker.get_usage()
    assert usage.input_tokens == 0
    assert usage.output_tokens > 0
    assert usage.output_tokens < 100


def test_tracker_delegates_events_correctly():
    parser = AnthropicAdapter().stream_parser()
    tracker = StreamUsageTracker(parser)

    line = _ant("content_block_delta", index=0, delta={"type": "text_delta", "text": "Hello"})
    events = tracker.feed(line)

    assert any(isinstance(e, TextDelta) and e.text == "Hello" for e in events)


def test_tracker_empty_stream_returns_zero_usage():
    parser = OpenAIAdapter().stream_parser()
    tracker = StreamUsageTracker(parser)
    tracker.finish()

    usage = tracker.get_usage()
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


class _ListParser:
    def __init__(self, batches):
        self._batches = list(batches)

    def feed(self, line):
        return self._batches.pop(0) if self._batches else []

    def finish(self):
        return []


def test_first_content_at_none_without_content():
    t = StreamUsageTracker(_ListParser([[MessageStart(model="m")], [TextDelta(index=0, text="")]]))
    t.feed("a")
    t.feed("b")
    assert t.first_content_at is None


def test_first_content_at_set_on_first_text(monkeypatch):
    clock = iter([10.0, 20.0])
    monkeypatch.setattr(
        "janus.streaming.usage.time",
        types.SimpleNamespace(monotonic=lambda: next(clock)),
        raising=False,
    )
    t = StreamUsageTracker(
        _ListParser([[TextDelta(index=0, text="hi")], [TextDelta(index=0, text="there")]])
    )
    t.feed("a")
    t.feed("b")
    assert t.first_content_at == 10.0


def test_reasoning_and_tool_events_count_as_content():
    for event in (
        ReasoningDelta(index=0, text="thinking"),
        ToolUseBlockStart(index=0, id="t1", name="f"),
        InputJsonDelta(index=0, partial_json="{"),
    ):
        t = StreamUsageTracker(_ListParser([[event]]))
        t.feed("a")
        assert t.first_content_at is not None


def test_empty_reasoning_signature_only_is_not_content():
    t = StreamUsageTracker(_ListParser([[ReasoningDelta(index=0, text="", signature="s")]]))
    t.feed("a")
    assert t.first_content_at is None

from pydantic import TypeAdapter

from janus.canonical.events import (
    CanonicalEvent,
    InputJsonDelta,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextBlockStart,
    TextDelta,
    ToolUseBlockStart,
)
from janus.canonical.models import Usage


def test_message_start():
    ev = MessageStart(model="gpt-4")
    assert ev.type == "message_start"
    assert ev.model == "gpt-4"


def test_text_block_start():
    ev = TextBlockStart(index=0)
    assert ev.type == "text_block_start"
    assert ev.index == 0


def test_tool_use_block_start():
    ev = ToolUseBlockStart(index=1, id="abc", name="read")
    assert ev.id == "abc"
    assert ev.name == "read"


def test_text_delta():
    ev = TextDelta(index=0, text="Hello")
    assert ev.text == "Hello"


def test_input_json_delta():
    ev = InputJsonDelta(index=1, partial_json='{"pa')
    assert ev.partial_json == '{"pa'


def test_message_delta_with_usage():
    ev = MessageDelta(stop_reason="end_turn", usage=Usage(input_tokens=10, output_tokens=5))
    assert ev.stop_reason == "end_turn"
    assert ev.usage.input_tokens == 10


def test_message_stop():
    ev = MessageStop()
    assert ev.type == "message_stop"


def test_event_discriminated_union():
    adapter = TypeAdapter(CanonicalEvent)
    ev = MessageStart(model="test")
    parsed = adapter.validate_python(ev.model_dump())
    assert isinstance(parsed, MessageStart)

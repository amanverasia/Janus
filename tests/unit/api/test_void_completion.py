"""Void/empty completion detection helpers (empty responses from reasoning relays)."""

from janus.api.routes import _event_produces_output, _is_void_response
from janus.canonical.events import (
    MessageDelta,
    MessageStart,
    ReasoningDelta,
    TextBlockStart,
    TextDelta,
    ToolUseBlockStart,
)
from janus.canonical.models import CanonicalResponse, Reasoning, TextPart, ToolUse, Usage


def _resp(content) -> CanonicalResponse:
    return CanonicalResponse(model="gpt-6-astra", content=content)


def test_empty_content_is_void():
    assert _is_void_response(_resp([])) is True


def test_reasoning_only_is_void():
    # Reasoning/thinking alone is not client-visible output.
    assert _is_void_response(_resp([Reasoning(text="thinking...")])) is True


def test_text_content_is_not_void():
    assert _is_void_response(_resp([TextPart(text="hello")])) is False


def test_tool_use_is_not_void():
    assert _is_void_response(_resp([ToolUse(id="call_1", name="ls", input={})])) is False


def test_void_response_uses_zero_usage():
    resp = _resp([])
    assert resp.usage == Usage()
    assert _is_void_response(resp) is True


def test_event_produces_output_positive():
    assert _event_produces_output(TextDelta(index=0, text="x")) is True
    assert _event_produces_output(TextBlockStart(index=0)) is True
    assert _event_produces_output(ToolUseBlockStart(index=0, id="call_1", name="ls")) is True


def test_event_produces_output_negative():
    # role-only / stop / reasoning events are not client-visible output.
    assert _event_produces_output(MessageStart(model="m")) is False
    assert _event_produces_output(MessageDelta(stop_reason="stop")) is False
    assert _event_produces_output(ReasoningDelta(index=0, text="thinking")) is False

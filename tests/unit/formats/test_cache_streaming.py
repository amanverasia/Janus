"""Streaming cache-token extraction and emit round-trips (issue #105).

The streaming billing path runs through ``StreamUsageTracker``, which collects
``MessageDelta.usage`` events emitted by each stream parser.  If a parser drops
cache fields, the tracker records them as zero and ``compute_cost`` then bills
cached tokens at the full input rate (or, for Anthropic, drops them entirely).
"""

import json

from janus.canonical.events import MessageDelta
from janus.canonical.models import CanonicalResponse, TextPart, Usage
from janus.formats.anthropic import AnthropicAdapter, AnthropicStreamParser
from janus.formats.gemini import GeminiAdapter, GeminiStreamParser
from janus.formats.openai import OpenAIAdapter, OpenAIStreamParser
from janus.formats.openai_responses import OpenAIResponsesAdapter, OpenAIResponsesStreamParser


def _events(parser, line: str):
    return parser.feed(line)


def _usage_deltas(events) -> list[Usage]:
    return [e.usage for e in events if isinstance(e, MessageDelta) and e.usage is not None]


# --- Anthropic streaming: message_start carries the disjoint cache breakdown ---


def test_anthropic_stream_message_start_normalizes_cache_usage():
    line = json.dumps(
        {
            "type": "message_start",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "usage": {
                    "input_tokens": 600,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 100,
                    "output_tokens": 0,
                },
            },
        }
    )
    deltas = _usage_deltas(_events(AnthropicStreamParser(), line))
    assert len(deltas) == 1
    usage = deltas[0]
    assert usage.input_tokens == 1000
    assert usage.cache_creation_input_tokens == 300
    assert usage.cache_read_input_tokens == 100


def test_anthropic_stream_message_start_without_cache_fields():
    line = json.dumps(
        {
            "type": "message_start",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 1000, "output_tokens": 0},
            },
        }
    )
    deltas = _usage_deltas(_events(AnthropicStreamParser(), line))
    assert deltas[0].input_tokens == 1000
    assert deltas[0].cache_creation_input_tokens == 0
    assert deltas[0].cache_read_input_tokens == 0


def test_anthropic_emit_response_roundtrips_disjoint_cache():
    resp = CanonicalResponse(
        model="claude-sonnet-4-20250514",
        content=[TextPart(type="text", text="ok")],
        stop_reason="end_turn",
        usage=Usage(
            input_tokens=1000,
            output_tokens=200,
            cache_creation_input_tokens=300,
            cache_read_input_tokens=100,
        ),
    )
    emitted = AnthropicAdapter().emit_response(resp)
    usage = emitted["usage"]
    # Downstream Anthropic clients expect the disjoint form: the non-cached
    # remainder plus the cache subsets.
    assert usage["input_tokens"] == 600
    assert usage["cache_creation_input_tokens"] == 300
    assert usage["cache_read_input_tokens"] == 100
    assert usage["output_tokens"] == 200


# --- OpenAI Chat streaming: final usage chunk carries cached_tokens ----------


def test_openai_chat_stream_usage_carries_cached_tokens():
    line = json.dumps(
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "choices": [],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_tokens_details": {"cached_tokens": 100},
            },
        }
    )
    deltas = _usage_deltas(_events(OpenAIStreamParser(), line))
    assert len(deltas) == 1
    assert deltas[0].input_tokens == 1000
    assert deltas[0].cache_read_input_tokens == 100


def test_openai_chat_emit_response_includes_cached_tokens_details():
    resp = CanonicalResponse(
        model="gpt-4o",
        content=[TextPart(type="text", text="ok")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=100),
    )
    emitted = OpenAIAdapter().emit_response(resp)
    usage = emitted["usage"]
    assert usage["prompt_tokens"] == 1000
    assert usage["prompt_tokens_details"]["cached_tokens"] == 100


# --- Gemini streaming: usageMetadata carries cachedContentTokenCount ----------


def test_gemini_stream_usage_carries_cached_content():
    line = json.dumps(
        {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 200,
                "cachedContentTokenCount": 100,
            },
        }
    )
    deltas = _usage_deltas(_events(GeminiStreamParser(), line))
    assert deltas
    usage = deltas[-1]
    assert usage.input_tokens == 1000
    assert usage.cache_read_input_tokens == 100


def test_gemini_emit_response_includes_cached_content():
    resp = CanonicalResponse(
        model="gemini-2.0-flash",
        content=[TextPart(type="text", text="ok")],
        stop_reason="STOP",
        usage=Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=100),
    )
    emitted = GeminiAdapter().emit_response(resp)
    meta = emitted["usageMetadata"]
    assert meta["promptTokenCount"] == 1000
    assert meta["cachedContentTokenCount"] == 100


# --- OpenAI Responses streaming: response.completed carries cached_tokens ----


def test_openai_responses_stream_final_carries_cached_tokens():
    line = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_x",
                "status": "completed",
                "model": "gpt-4o",
                "output": [],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "input_tokens_details": {"cached_tokens": 100},
                },
            },
        }
    )
    parser = OpenAIResponsesStreamParser()
    deltas = _usage_deltas(_events(parser, line))
    assert len(deltas) == 1
    assert deltas[0].input_tokens == 1000
    assert deltas[0].cache_read_input_tokens == 100


def test_openai_responses_emit_response_includes_cached_tokens():
    resp = CanonicalResponse(
        model="gpt-4o",
        content=[TextPart(type="text", text="ok")],
        stop_reason="end_turn",
        usage=Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=100),
    )
    emitted = OpenAIResponsesAdapter().emit_response(resp)
    usage = emitted["usage"]
    assert usage["input_tokens"] == 1000
    assert usage["input_tokens_details"]["cached_tokens"] == 100

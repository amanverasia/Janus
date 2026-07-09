import pytest

from janus.formats.openai import OpenAIStreamParser
from janus.streaming.passthrough import (
    fix_invalid_id,
    generic_sse_passthrough,
    has_valuable_content,
    normalize_openai_chunk,
    openai_passthrough_stream,
    parse_sse_data_line,
)
from janus.streaming.usage import StreamUsageTracker


def test_parse_sse_data_line() -> None:
    assert parse_sse_data_line("") is None
    kind, payload = parse_sse_data_line('data: {"a":1}')  # type: ignore[misc]
    assert kind == "data"
    assert payload == {"a": 1}
    assert parse_sse_data_line("data: [DONE]") == ("data", "[DONE]")
    assert parse_sse_data_line(": keep-alive") == ("meta", ": keep-alive")
    assert parse_sse_data_line("event: message") == ("meta", "event: message")


def test_normalize_injects_object_created_and_fixes_id() -> None:
    chunk = {"id": "chat", "choices": [{"index": 0, "delta": {}, "finish_reason": None}]}
    out = normalize_openai_chunk(chunk)
    assert out["object"] == "chat.completion.chunk"
    assert isinstance(out["created"], int)
    assert out["id"].startswith("chatcmpl-")
    assert fix_invalid_id({"id": "completion"}) is True


def test_normalize_strips_empty_tool_calls_and_azure_filters() -> None:
    chunk = {
        "id": "chatcmpl-abc12345",
        "prompt_filter_results": [],
        "choices": [
            {
                "index": 0,
                "delta": {"content": "hi", "tool_calls": []},
                "content_filter_results": {"hate": {"filtered": False}},
                "finish_reason": None,
            }
        ],
    }
    out = normalize_openai_chunk(chunk)
    assert "prompt_filter_results" not in out
    assert "content_filter_results" not in out["choices"][0]
    assert "tool_calls" not in out["choices"][0]["delta"]
    assert has_valuable_content(out) is True
    empty = {
        "id": "chatcmpl-abc12345",
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
    }
    assert has_valuable_content(normalize_openai_chunk(empty)) is False


@pytest.mark.asyncio
async def test_passthrough_restores_framing_and_preserves_finish() -> None:
    async def lines():
        yield (
            'data: {"id":"chatcmpl-abc12345","object":"chat.completion.chunk",'
            '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}'
        )
        yield ""
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{"content":"hi"},'
            '"finish_reason":null}]}'
        )
        yield ""
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{},'
            '"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,'
            '"total_tokens":4}}'
        )
        yield ""
        yield "data: [DONE]"
        yield ""

    tracker = StreamUsageTracker(OpenAIStreamParser())
    out = b"".join(
        [
            c
            async for c in openai_passthrough_stream(
                lines(), tracker=tracker, model="deepseek-v4-pro", provider="deepseek"
            )
        ]
    )
    text = out.decode()
    assert "\n\n" in text
    assert '"finish_reason":"stop"' in text or '"finish_reason": "stop"' in text
    assert "data: [DONE]" in text
    # Should not invent a second synthetic finish when upstream finished
    assert text.count("chatcmpl-janus") == 0
    usage = tracker.get_usage()
    assert usage.input_tokens == 3
    assert usage.output_tokens == 1


@pytest.mark.asyncio
async def test_passthrough_synthesizes_finish_and_done() -> None:
    async def lines():
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{"content":"hi"},'
            '"finish_reason":null}]}'
        )
        yield ""

    tracker = StreamUsageTracker(OpenAIStreamParser())
    out = b"".join(
        [
            c
            async for c in openai_passthrough_stream(
                lines(), tracker=tracker, model="m", provider="deepseek"
            )
        ]
    )
    text = out.decode()
    assert "chatcmpl-janus" in text
    assert "finish_reason" in text
    assert "data: [DONE]" in text
    # Valid final event framing
    assert text.endswith("\n\n") or text.rstrip().endswith("[DONE]")


@pytest.mark.asyncio
async def test_gemini_family_skips_done_sentinel() -> None:
    async def lines():
        yield 'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":"stop"}]}'
        yield ""

    out = b"".join(
        [
            c
            async for c in openai_passthrough_stream(
                lines(), model="g", provider="gemini", ensure_finish=False
            )
        ]
    )
    assert b"[DONE]" not in out


@pytest.mark.asyncio
async def test_generic_passthrough_framing() -> None:
    async def lines():
        yield "event: content_block_delta"
        yield 'data: {"type":"content_block_delta","delta":{"text":"hi"}}'
        yield ""

    out = b"".join([c async for c in generic_sse_passthrough(lines())])
    text = out.decode()
    assert "event: content_block_delta\n" in text
    assert "data: " in text
    assert "\n\n" in text


@pytest.mark.asyncio
async def test_drops_non_json_data_garbage() -> None:
    async def lines():
        yield "data: <html>rate limited</html>"
        yield ""
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{"content":"ok"},'
            '"finish_reason":"stop"}]}'
        )
        yield ""

    out = b"".join(
        [c async for c in openai_passthrough_stream(lines(), model="m", provider="openai")]
    )
    text = out.decode()
    assert "<html>" not in text
    assert "ok" in text


@pytest.mark.asyncio
async def test_skips_empty_delta_chunks() -> None:
    async def lines():
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{},'
            '"finish_reason":null}]}'
        )
        yield ""
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{"content":"x"},'
            '"finish_reason":null}]}'
        )
        yield ""
        yield (
            'data: {"id":"chatcmpl-abc12345","choices":[{"index":0,"delta":{},'
            '"finish_reason":"stop"}]}'
        )
        yield ""

    text = b"".join(
        [c async for c in openai_passthrough_stream(lines(), model="m", provider="deepseek")]
    ).decode()
    # One content + one finish (+ DONE). Empty delta should not appear as its own event.
    events = [e for e in text.split("\n\n") if e.strip() and "[DONE]" not in e]
    assert len(events) == 2
    assert any('"content":"x"' in e or '"content": "x"' in e for e in events)
    assert any("finish_reason" in e and "stop" in e for e in events)

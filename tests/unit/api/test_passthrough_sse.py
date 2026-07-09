"""Compatibility tests — helpers live in janus.streaming.passthrough now."""

import pytest

from janus.formats.openai import OpenAIStreamParser
from janus.streaming.passthrough import openai_passthrough_stream
from janus.streaming.usage import StreamUsageTracker


@pytest.mark.asyncio
async def test_routes_use_streaming_passthrough_module() -> None:
    from janus.api import routes

    assert hasattr(routes, "openai_passthrough_stream")
    assert hasattr(routes, "generic_sse_passthrough")


@pytest.mark.asyncio
async def test_end_to_end_openai_passthrough_shape() -> None:
    async def lines():
        yield (
            'data: {"id":"chatcmpl-xyz12345","choices":[{"index":0,"delta":{"content":"A"},'
            '"finish_reason":null}]}'
        )
        yield ""
        yield (
            'data: {"id":"chatcmpl-xyz12345","choices":[{"index":0,"delta":{},'
            '"finish_reason":"stop"}]}'
        )
        yield ""
        yield "data: [DONE]"
        yield ""

    tracker = StreamUsageTracker(OpenAIStreamParser())
    chunks = [
        c
        async for c in openai_passthrough_stream(
            lines(), tracker=tracker, model="deepseek-v4-pro", provider="deepseek"
        )
    ]
    text = b"".join(chunks).decode()
    events = [e for e in text.split("\n\n") if e.strip()]
    assert any("finish_reason" in e and "stop" in e for e in events)
    assert any("[DONE]" in e for e in events)

import pytest

from janus.formats.anthropic import AnthropicAdapter
from janus.formats.openai import OpenAIAdapter
from janus.streaming.translator import translate_stream


async def _anthropic_lines():
    """Simulate an Anthropic SSE upstream stream."""
    events = [
        '{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","usage":{"input_tokens":0,"output_tokens":0}}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}',
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}',
        '{"type":"message_stop"}',
    ]
    for ev in events:
        yield ev


@pytest.mark.asyncio
async def test_translate_stream_anthropic_to_openai():
    """Cross-format: Anthropic upstream -> OpenAI client output."""
    upstream = _anthropic_lines()
    parser = AnthropicAdapter().stream_parser()
    emitter = OpenAIAdapter().stream_emitter()

    chunks: list[bytes] = []
    async for chunk in translate_stream(upstream, parser, emitter):
        chunks.append(chunk)

    output = b"".join(chunks).decode()
    assert "chat.completion.chunk" in output
    assert "Hello" in output
    assert "world" in output
    assert "[DONE]" in output


@pytest.mark.asyncio
async def test_translate_stream_openai_to_openai():
    """Same-format: OpenAI upstream -> OpenAI client output."""

    async def _openai_lines():
        lines = [
            '{"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
            '{"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}',
            '{"id":"r1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
            "[DONE]",
        ]
        for line in lines:
            yield line

    upstream = _openai_lines()
    parser = OpenAIAdapter().stream_parser()
    emitter = OpenAIAdapter().stream_emitter()

    chunks: list[bytes] = []
    async for chunk in translate_stream(upstream, parser, emitter):
        chunks.append(chunk)

    output = b"".join(chunks).decode()
    assert "Hi" in output
    assert "[DONE]" in output


@pytest.mark.asyncio
async def test_translate_stream_anthropic_to_anthropic():
    """Same-format: Anthropic upstream -> Anthropic client output."""
    upstream = _anthropic_lines()
    parser = AnthropicAdapter().stream_parser()
    emitter = AnthropicAdapter().stream_emitter()

    chunks: list[bytes] = []
    async for chunk in translate_stream(upstream, parser, emitter):
        chunks.append(chunk)

    output = b"".join(chunks).decode()
    assert "message_start" in output
    assert "text_delta" in output
    assert "Hello" in output
    assert "message_stop" in output


@pytest.mark.asyncio
async def test_translate_stream_empty():
    """Empty upstream should still produce emitter finish output."""

    async def _empty():
        return
        yield  # make it an async generator

    parser = OpenAIAdapter().stream_parser()
    emitter = OpenAIAdapter().stream_emitter()

    chunks: list[bytes] = []
    async for chunk in translate_stream(_empty(), parser, emitter):
        chunks.append(chunk)

    output = b"".join(chunks).decode()
    assert "[DONE]" in output

from __future__ import annotations

from collections.abc import AsyncIterator

from janus.formats.base import StreamEmitter, StreamParser


async def translate_stream(
    upstream_lines: AsyncIterator[str],
    parser: StreamParser,
    emitter: StreamEmitter,
) -> AsyncIterator[bytes]:
    """Translate an upstream provider's SSE stream to the client's format.

    upstream_lines -> parser (-> canonical events) -> emitter (-> client bytes)
    """
    async for line in upstream_lines:
        if not line or not line.strip():
            continue
        data = line
        if line.startswith("data: "):
            data = line[6:]
        elif line.startswith("data:"):
            data = line[5:]
        for event in parser.feed(data):
            for chunk in emitter.feed(event):
                yield chunk
    for event in parser.finish():
        for chunk in emitter.feed(event):
            yield chunk
    for chunk in emitter.finish():
        yield chunk

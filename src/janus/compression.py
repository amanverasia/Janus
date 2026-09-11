"""Selective gzip for large, fully-buffered responses.

Dashboard state payloads are big and highly compressible: the models section
measured 1168 KB on a live deployment and gzips to 33 KB. Janus is also a
streaming gateway -- SSE for live usage, chunked completions for the LLM proxy
-- and compressing a stream buffers it, which destroys the latency that makes
streaming worth having.

The rule that keeps both true: only compress a response that already declared a
``content-length``. A streamed response never does, so it is excluded by
construction rather than by a content-type blocklist that could miss a case.
"""

from __future__ import annotations

import gzip
from collections.abc import Awaitable, Callable
from typing import Any

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

# Types worth the CPU. Everything else (images, video, wasm, already-gzipped
# archives) is either incompressible or already compressed.
COMPRESSIBLE_PREFIXES = (
    "application/json",
    "application/javascript",
    "application/xml",
    "text/",
)

# Never compress, even though it is text: buffering defeats the stream.
EXCLUDED_TYPES = ("text/event-stream",)

DEFAULT_MINIMUM_SIZE = 1024


class SelectiveGZipMiddleware:
    """gzip large buffered JSON/text; leave streamed responses alone."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        minimum_size: int = DEFAULT_MINIMUM_SIZE,
        compresslevel: int = 6,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _accepts_gzip(scope):
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        body = bytearray()
        streamed = False

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message, streamed

            if streamed:
                await send(message)
                return

            if message["type"] == "http.response.start":
                start_message = message
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            assert start_message is not None
            headers = _Headers(start_message.get("headers", []))

            # Decided once, on the first body part.
            if not body and not _should_compress(headers, self.minimum_size):
                streamed = True
                await send(start_message)
                await send(message)
                return

            body.extend(message.get("body", b""))
            if message.get("more_body", False):
                return

            payload = gzip.compress(bytes(body), compresslevel=self.compresslevel)
            headers.set(b"content-encoding", b"gzip")
            headers.set(b"content-length", str(len(payload)).encode())
            headers.append_vary(b"Accept-Encoding")
            start_message["headers"] = headers.raw
            await send(start_message)
            await send({"type": "http.response.body", "body": payload, "more_body": False})

        await self.app(scope, receive, send_wrapper)


def _accepts_gzip(scope: Scope) -> bool:
    for key, value in scope.get("headers", []):
        if key.lower() == b"accept-encoding":
            return b"gzip" in value.lower()
    return False


def _should_compress(headers: _Headers, minimum_size: int) -> bool:
    if headers.get(b"content-encoding") is not None:
        return False  # already encoded; do not double-compress

    # A streamed response declares no content-length. Excluding those keeps SSE
    # and chunked completions untouched without enumerating content types.
    raw_length = headers.get(b"content-length")
    if raw_length is None:
        return False
    try:
        if int(raw_length) < minimum_size:
            return False
    except ValueError:
        return False

    content_type = (headers.get(b"content-type") or b"").decode("latin-1").lower()
    base_type = content_type.split(";", 1)[0].strip()
    if base_type in EXCLUDED_TYPES:
        return False
    return base_type.startswith(COMPRESSIBLE_PREFIXES)


class _Headers:
    """Small mutable view over a raw ASGI header list."""

    def __init__(self, raw: list[tuple[bytes, bytes]]) -> None:
        self.raw = list(raw)

    def get(self, name: bytes) -> bytes | None:
        lowered = name.lower()
        for key, value in self.raw:
            if key.lower() == lowered:
                return value
        return None

    def set(self, name: bytes, value: bytes) -> None:
        lowered = name.lower()
        self.raw = [(k, v) for k, v in self.raw if k.lower() != lowered]
        self.raw.append((name, value))

    def append_vary(self, field: bytes) -> None:
        existing = self.get(b"vary")
        if existing is None:
            self.set(b"vary", field)
        elif field.lower() not in existing.lower():
            self.set(b"vary", existing + b", " + field)

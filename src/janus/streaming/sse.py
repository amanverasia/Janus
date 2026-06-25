import json
from collections.abc import Iterator
from typing import Any


def encode_sse(data: dict[str, Any]) -> bytes:
    text = json.dumps(data, separators=(",", ":"))
    if "\n" in text:
        return b"".join(b"data: " + line.encode() + b"\n" for line in text.splitlines()) + b"\n"
    return b"data: " + text.encode() + b"\n\n"


def encode_done() -> bytes:
    return b"data: [DONE]\n\n"


def parse_sse_lines(raw: bytes) -> Iterator[str]:
    buffer: list[str] = []
    for line in raw.split(b"\n"):
        stripped = line.rstrip(b"\r")
        if not stripped:
            if buffer:
                yield "".join(buffer)
                buffer = []
            continue
        if stripped.startswith(b"data: "):
            data = stripped[6:].decode()
            if data.strip() == "[DONE]":
                yield "[DONE]"
                continue
            buffer.append(data)
    if buffer:
        yield "".join(buffer)

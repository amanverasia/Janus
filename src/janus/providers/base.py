from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RawResult:
    status_code: int
    json_data: dict[str, Any] | None = None
    lines: AsyncIterator[str] | None = None


def parse_error_body(body: bytes) -> dict[str, Any]:
    """Best-effort parse of an upstream error body into a dict for RawResult."""
    if not body:
        return {"error": "Upstream error"}
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"error": body.decode(errors="replace")[:500]}
    if isinstance(parsed, dict):
        return parsed
    return {"error": parsed}


class Provider(Protocol):
    name: str

    async def call(self, payload: dict[str, Any], stream: bool) -> RawResult: ...

    async def close(self) -> None: ...

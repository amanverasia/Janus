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
    retry_after: float | None = None


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


def parse_retry_after(headers: Any) -> float | None:
    try:
        raw = headers.get("retry-after") if hasattr(headers, "get") else None
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class Provider(Protocol):
    name: str

    async def call(self, payload: dict[str, Any], stream: bool) -> RawResult: ...

    async def close(self) -> None: ...

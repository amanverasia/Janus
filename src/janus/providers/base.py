from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
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
    """Parse rate-limit reset headers into a delay in seconds.

    Checks Retry-After (delay-seconds or HTTP-date), then the common
    x-ratelimit-reset-after (delta seconds) and x-ratelimit-reset (epoch)
    variants some OAuth backends send instead. Returns the delay in seconds
    (>= 0), or None when absent/unparseable.
    """
    if not hasattr(headers, "get"):
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:
        return None
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            dt = None
        if dt is not None:
            return max(0.0, dt.timestamp() - time.time())
    try:
        reset_after = headers.get("x-ratelimit-reset-after")
        if reset_after is not None:
            secs = float(reset_after)
            if secs > 0:
                return secs
    except (TypeError, ValueError):
        pass
    try:
        reset_at = headers.get("x-ratelimit-reset")
        if reset_at is not None:
            delay = float(reset_at) - time.time()
            if delay > 0:
                return delay
    except (TypeError, ValueError):
        pass
    return None


_GOOGLE_RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"
_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$")
_RETRY_MESSAGE_RE = re.compile(
    r"(?:retry in|reset(?:s)? after)\s+((?:\d+h)?(?:\d+m)?(?:\d+(?:\.\d+)?s)?)",
    re.IGNORECASE,
)


def _parse_duration(raw: Any) -> float | None:
    """Parse a compact duration string like "39s", "1.5s" or "1h2m3s" to seconds."""
    if not isinstance(raw, str) or not raw:
        return None
    match = _DURATION_RE.match(raw.strip())
    if match is None or not any(match.groups()):
        return None
    hours, minutes, seconds = match.groups()
    total = 0.0
    if hours:
        total += float(hours) * 3600
    if minutes:
        total += float(minutes) * 60
    if seconds:
        total += float(seconds)
    return total if total > 0 else None


def parse_google_retry_info(body: Any | None) -> float | None:
    """Extract a retry delay (seconds) from a Google RPC error body.

    Google backends (Gemini API, Cloud Code / Antigravity) attach
    ``google.rpc.RetryInfo`` details with a ``retryDelay`` duration string
    (e.g. "39s") to 429 bodies instead of sending a Retry-After header.
    Falls back to "retry in Ns" / "reset after XhYmZs" phrases inside the
    error message. Mirrors 9router's gemini-cli/antigravity executors.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    details = error.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            if detail.get("@type") != _GOOGLE_RETRY_INFO_TYPE:
                continue
            delay = _parse_duration(detail.get("retryDelay"))
            if delay is not None:
                return delay
    message = error.get("message")
    if isinstance(message, str) and message:
        match = _RETRY_MESSAGE_RE.search(message)
        if match:
            return _parse_duration(match.group(1))
    return None


class Provider(Protocol):
    name: str

    async def call(self, payload: dict[str, Any], stream: bool) -> RawResult: ...

    async def close(self) -> None: ...

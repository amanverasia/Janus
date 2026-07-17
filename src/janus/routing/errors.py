from __future__ import annotations

import enum
import json
from typing import Any

import httpx


class ErrorType(enum.StrEnum):
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    AUTH_ERROR = "auth_error"
    PAYMENT_ERROR = "payment_error"
    NETWORK = "network"
    CLIENT_ERROR = "client_error"
    UNKNOWN = "unknown"


def classify_error(status_code: int) -> ErrorType:
    if status_code == 429:
        return ErrorType.RATE_LIMIT
    if status_code >= 500:
        return ErrorType.SERVER_ERROR
    if status_code in (401, 403):
        return ErrorType.AUTH_ERROR
    if status_code == 402:
        return ErrorType.PAYMENT_ERROR
    if status_code >= 400:
        return ErrorType.CLIENT_ERROR
    return ErrorType.UNKNOWN


def is_fallback_eligible(error: int | Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(error, int):
        return error in (429, 401, 403, 402) or error >= 500
    return False


BODY_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "rate limit",
    "too many requests",
    "quota exceeded",
    "capacity",
    "overloaded",
    "resource exhausted",
)

# OpenRouter (and similar gateways) return 400/404 when no upstream matches the
# account's privacy/data-collection settings, or when assistant-prefill is
# rejected. These should rotate to the next inventory key, not fail hard.
BODY_PROVIDER_MISS_MARKERS: tuple[str, ...] = (
    "no endpoints found",
    "no endpoints",
    "no providers available",
    "no provider",
    "data collection",
    "data_collection",
    "privacy",
    "provider routing",
    "not available for your",
    "assistant prefill",
    "prefill",
    "last message must be a user",
    "must end with a user message",
    "messages must alternate",
)


def _error_body_text(body: Any | None) -> str:
    if not body:
        return ""
    if not isinstance(body, dict):
        # Upstream providers don't always wrap errors in {"error": ...} -
        # some return a bare string, a list of error objects, or (rarely) a
        # number. Serialize whatever we got so marker matching still works,
        # without ever raising.
        if isinstance(body, str):
            text = body
        else:
            try:
                text = json.dumps(body)
            except Exception:
                try:
                    text = str(body)
                except Exception:
                    return ""
        return text.lower().replace("_", " ")[:2000]
    error = body.get("error")
    if error is None:
        # OpenRouter sometimes puts the message at the top level.
        try:
            text = json.dumps(body)
        except Exception:
            try:
                text = str(body)
            except Exception:
                return ""
        return text.lower().replace("_", " ")[:2000]
    if isinstance(error, str):
        text = error
    else:
        try:
            text = str(error)
        except Exception:
            return ""
    # Normalize enum-style codes like "RESOURCE_EXHAUSTED" so they match the
    # space-separated markers below.
    return text.lower().replace("_", " ")[:2000]


def refine_error_type(status_code: int, body: Any | None) -> ErrorType:
    """Classify by status, then upgrade to RATE_LIMIT if the body text says so.

    Some providers disguise rate limits as a 400 (or even 200-wrapped) error
    with a body like {"error": "quota exceeded"}. Status-only classification
    misses these, so we also inspect the body text for well-known markers.
    """
    error_type = classify_error(status_code)
    text = _error_body_text(body)
    if text and any(marker in text for marker in BODY_RATE_LIMIT_MARKERS):
        return ErrorType.RATE_LIMIT
    # Treat privacy / no-endpoint / prefill rejections as soft server errors so
    # FallbackHandler applies a short cooldown and tries the next account.
    if (
        status_code in (400, 404)
        and text
        and any(marker in text for marker in BODY_PROVIDER_MISS_MARKERS)
    ):
        return ErrorType.SERVER_ERROR
    return error_type


def is_fallback_eligible_refined(status_code: int, body: Any | None) -> bool:
    if is_fallback_eligible(status_code):
        return True
    refined = refine_error_type(status_code, body)
    return refined in (ErrorType.RATE_LIMIT, ErrorType.SERVER_ERROR) and status_code in (
        400,
        404,
    )


BACKOFF_BASE_MS = 2000
BACKOFF_MAX_S = 300.0
BACKOFF_MAX_LEVEL = 15
RETRY_AFTER_CAP_S = 1800.0

FIXED_COOLDOWNS: dict[str, float] = {
    "server_error": 30.0,
    "auth_error": 300.0,
    "payment_error": 300.0,
    "network": 15.0,
}


def get_cooldown(error_type: str, backoff_level: int = 0) -> tuple[float, int]:
    if error_type == "rate_limit":
        new_level = min(backoff_level + 1, BACKOFF_MAX_LEVEL)
        secs = min(BACKOFF_BASE_MS * (2 ** (new_level - 1)) / 1000, BACKOFF_MAX_S)
        return secs, new_level
    return FIXED_COOLDOWNS.get(error_type, 60.0), 0

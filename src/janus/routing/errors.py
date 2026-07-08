from __future__ import annotations

import enum

import httpx


class ErrorType(enum.StrEnum):
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    AUTH_ERROR = "auth_error"
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
    if status_code >= 400:
        return ErrorType.CLIENT_ERROR
    return ErrorType.UNKNOWN


def is_fallback_eligible(error: int | Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(error, int):
        return error in (429, 401, 403) or error >= 500
    return False


BACKOFF_BASE_MS = 2000
BACKOFF_MAX_S = 300.0
BACKOFF_MAX_LEVEL = 15
RETRY_AFTER_CAP_S = 1800.0

FIXED_COOLDOWNS: dict[str, float] = {
    "server_error": 30.0,
    "auth_error": 300.0,
    "network": 15.0,
}


def get_cooldown(error_type: str, backoff_level: int = 0) -> tuple[float, int]:
    if error_type == "rate_limit":
        new_level = min(backoff_level + 1, BACKOFF_MAX_LEVEL)
        secs = min(BACKOFF_BASE_MS * (2 ** (new_level - 1)) / 1000, BACKOFF_MAX_S)
        return secs, new_level
    return FIXED_COOLDOWNS.get(error_type, 60.0), 0

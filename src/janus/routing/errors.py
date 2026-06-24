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

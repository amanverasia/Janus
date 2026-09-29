from __future__ import annotations

from time import monotonic

from fastapi import Request

LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 300.0
LOGIN_MAX_TRACKED_CLIENTS = 4096
UNKNOWN_CLIENT = "unknown"

_failures: dict[str, tuple[int, float]] = {}


def client_identity(request: Request) -> str:
    if request.client is None or not request.client.host:
        return UNKNOWN_CLIENT
    return request.client.host


def is_login_locked(client_id: str) -> bool:
    entry = _failures.get(client_id)
    if entry is None:
        return False
    count, reset_at = entry
    if monotonic() >= reset_at:
        del _failures[client_id]
        return False
    return count >= LOGIN_MAX_FAILURES


def record_login_failure(client_id: str) -> None:
    count, reset_at = _failures.get(client_id, (0, 0.0))
    now = monotonic()
    if now >= reset_at:
        _failures[client_id] = (1, now + LOGIN_FAILURE_WINDOW_SECONDS)
    else:
        _failures[client_id] = (count + 1, reset_at)
    _bound_entries()


def clear_login_failures(client_id: str) -> None:
    _failures.pop(client_id, None)


def prune_login_failures() -> None:
    now = monotonic()
    expired = [client_id for client_id, (_, reset_at) in _failures.items() if now >= reset_at]
    for client_id in expired:
        del _failures[client_id]


def reset_login_throttle() -> None:
    _failures.clear()


def _bound_entries() -> None:
    if len(_failures) <= LOGIN_MAX_TRACKED_CLIENTS:
        return
    prune_login_failures()
    while len(_failures) > LOGIN_MAX_TRACKED_CLIENTS:
        oldest = min(_failures, key=lambda client_id: _failures[client_id][1])
        del _failures[oldest]

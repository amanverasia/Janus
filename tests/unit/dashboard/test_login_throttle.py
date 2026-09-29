from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Address

from janus.dashboard import login_throttle


def _request(host: str | None) -> SimpleNamespace:
    client = None if host is None else Address(host, 12345)
    return SimpleNamespace(client=client)


def test_client_identity_uses_client_host_or_fallback() -> None:
    assert login_throttle.client_identity(_request("203.0.113.7")) == "203.0.113.7"
    assert login_throttle.client_identity(_request(None)) == login_throttle.UNKNOWN_CLIENT


def test_lockout_after_max_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_throttle, "LOGIN_MAX_FAILURES", 3)
    login_throttle.record_login_failure("203.0.113.7")
    assert login_throttle.is_login_locked("203.0.113.7") is False
    login_throttle.record_login_failure("203.0.113.7")
    assert login_throttle.is_login_locked("203.0.113.7") is False
    login_throttle.record_login_failure("203.0.113.7")
    assert login_throttle.is_login_locked("203.0.113.7") is True
    assert login_throttle.is_login_locked("198.51.100.9") is False


def test_window_expiry_unlocks_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_throttle, "LOGIN_MAX_FAILURES", 2)
    login_throttle.record_login_failure("203.0.113.7")
    login_throttle.record_login_failure("203.0.113.7")
    assert login_throttle.is_login_locked("203.0.113.7") is True
    now = login_throttle.monotonic()
    monkeypatch.setattr(login_throttle, "monotonic", lambda: now + 301.0)
    assert login_throttle.is_login_locked("203.0.113.7") is False


def test_success_clears_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_throttle, "LOGIN_MAX_FAILURES", 2)
    login_throttle.record_login_failure("203.0.113.7")
    login_throttle.clear_login_failures("203.0.113.7")
    assert login_throttle.is_login_locked("203.0.113.7") is False
    login_throttle.record_login_failure("203.0.113.7")
    assert login_throttle.is_login_locked("203.0.113.7") is False


def test_prune_removes_expired_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    login_throttle.record_login_failure("203.0.113.7")
    now = login_throttle.monotonic()
    monkeypatch.setattr(login_throttle, "monotonic", lambda: now + 301.0)
    login_throttle.prune_login_failures()
    assert login_throttle._failures == {}


def test_entries_bounded_by_max_tracked_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_throttle, "LOGIN_MAX_TRACKED_CLIENTS", 4)
    for index in range(6):
        login_throttle.record_login_failure(f"203.0.113.{index}")
    assert len(login_throttle._failures) == 4
    assert "203.0.113.0" not in login_throttle._failures
    assert "203.0.113.5" in login_throttle._failures

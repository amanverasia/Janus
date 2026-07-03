from __future__ import annotations

import time

from janus.dashboard.credentials import (
    create_session_token,
    hash_password,
    verify_password,
    verify_session_token,
)


def test_hash_and_verify_password() -> None:
    stored = hash_password("secret-pass")
    assert verify_password("secret-pass", stored) is True
    assert verify_password("wrong-pass", stored) is False


def test_session_token_round_trip() -> None:
    secret = "test-secret"
    token = create_session_token(secret, "admin", ttl=3600)
    assert verify_session_token(secret, token) == "admin"


def test_session_token_rejects_tampered_signature() -> None:
    secret = "test-secret"
    token = create_session_token(secret, "admin", ttl=3600)
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert verify_session_token(secret, tampered) is None


def test_session_token_rejects_expired_token() -> None:
    secret = "test-secret"
    token = create_session_token(secret, "admin", ttl=-1)
    time.sleep(0.01)
    assert verify_session_token(secret, token) is None

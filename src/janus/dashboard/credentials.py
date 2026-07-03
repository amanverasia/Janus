from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from janus.storage.settings import get_setting, set_setting

SESSION_COOKIE = "janus_dashboard_session"
SETTINGS_USERNAME = "dashboard_username"
SETTINGS_PASSWORD_HASH = "dashboard_password_hash"
SETTINGS_SESSION_SECRET = "dashboard_session_secret"
PBKDF2_ITERATIONS = 100_000
SESSION_TTL_SECONDS = 30 * 86400


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session_token(secret: str, username: str, ttl: int = SESSION_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + ttl
    payload = f"{username}:{expires_at}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(secret: str, token: str) -> str | None:
    try:
        username, expires_at_str, signature = token.rsplit(":", 2)
        payload = f"{username}:{expires_at_str}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if int(expires_at_str) < int(time.time()):
            return None
        return username
    except (ValueError, TypeError):
        return None


async def get_or_create_session_secret(db_path: str | Path) -> str:
    existing = await get_setting(db_path, SETTINGS_SESSION_SECRET)
    if existing:
        return existing
    secret = secrets.token_hex(32)
    await set_setting(db_path, SETTINGS_SESSION_SECRET, secret)
    return secret


async def is_password_login_configured(db_path: str | Path) -> bool:
    username = await get_setting(db_path, SETTINGS_USERNAME)
    password_hash = await get_setting(db_path, SETTINGS_PASSWORD_HASH)
    return bool(username and password_hash)

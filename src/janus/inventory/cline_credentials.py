"""Normalize / refresh Cline (api.cline.bot) WorkOS OAuth credentials.

Cline is an OpenAI-compatible coding-agent gateway. Its keys are WorkOS JWTs
that must be presented to the gateway as ``workos:<jwt>``:

    Authorization: Bearer workos:<jwt>

Access tokens live ~1h; refresh tokens are long-lived opaque strings exchanged
at the WorkOS user-management authenticate endpoint.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

CLINE_BASE_URL = "https://api.cline.bot/api/v1"
CLINE_IDENTITY_URL = "https://api.cline.bot/api/v1/users/me"

WORKOS_AUTH_URL = "https://api.workos.com/user_management/authenticate"
WORKOS_CLIENT_ID = "client_01K3A541FN8TA3EPPHTD2325AR"

# Present on a dead session. Mirrors codex/kiro failure semantics: archive, don't retry.
SESSION_ENDED_MARKERS = ("Session has already ended", "invalid_grant")


def _strip_workos_prefix(token: str) -> str:
    return token[7:] if token.startswith("workos:") else token


def credential_value(access_token: str) -> str:
    """Return the key_value form Cline expects (``workos:`` prefixed)."""
    return access_token if access_token.startswith("workos:") else f"workos:{access_token}"


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_cline_credential(raw: str) -> tuple[str, dict[str, Any] | None]:
    text = raw.strip()
    if not text.startswith("{"):
        return credential_value(text), None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Cline credential JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Cline credential JSON must be an object")
    access = _first_str(data, "accessToken", "access_token", "token", "apiKey", "api_key")
    if not access:
        raise ValueError("Cline credential missing access token")
    metadata: dict[str, Any] = {}
    refresh = _first_str(data, "refreshToken", "refresh_token")
    if refresh:
        metadata["refresh_token"] = refresh
    email = _first_str(data, "email")
    if email:
        metadata["email"] = email
    return credential_value(access), metadata or None


async def probe_cline(access_token: str, client: httpx.AsyncClient | None = None) -> int | None:
    """Check an access token against Cline's identity endpoint."""
    own_client = client is None
    http = client if client is not None else httpx.AsyncClient(timeout=20.0)
    try:
        r = await http.get(
            CLINE_IDENTITY_URL,
            headers={"Authorization": f"Bearer {credential_value(access_token)}"},
        )
        return r.status_code
    except (httpx.TimeoutException, httpx.RequestError):
        return None
    finally:
        if own_client:
            await http.aclose()


async def refresh_cline(
    refresh_token: str,
    *,
    client_id: str = WORKOS_CLIENT_ID,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Exchange a WorkOS refresh token for a fresh Cline access token.

    Returns ``{"access_token", "refresh_token", "email", "user_id"}`` or ``None``
    when the session is dead/unusable.
    """
    own_client = client is None
    http = client if client is not None else httpx.AsyncClient(timeout=20.0)
    try:
        r = await http.post(
            WORKOS_AUTH_URL,
            json={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if r.status_code >= 400:
            return None
        data = r.json()
    except (httpx.TimeoutException, httpx.RequestError, ValueError):
        return None
    finally:
        if own_client:
            await http.aclose()

    user = data.get("user") if isinstance(data, dict) else None
    if isinstance(user, dict):
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", refresh_token),
            "email": user.get("email"),
            "user_id": user.get("id"),
        }
    return {
        "access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token", refresh_token),
        "email": None,
        "user_id": None,
    }

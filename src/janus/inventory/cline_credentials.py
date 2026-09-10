"""Normalize / refresh Cline (api.cline.bot) WorkOS OAuth credentials.

Cline is an OpenAI-compatible coding-agent gateway. Its keys are WorkOS JWTs
that must be presented to the gateway as ``workos:<jwt>``:

    Authorization: Bearer workos:<jwt>

Access tokens live ~1h; refresh tokens are long-lived opaque strings exchanged
at the WorkOS user-management authenticate endpoint.
"""

from __future__ import annotations

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

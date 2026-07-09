"""Shared OAuth credential helpers for subscription providers.

Credentials are stored in ``providers.api_key`` either as a bare access token
or as a JSON blob::

    {
      "access_token": "...",
      "refresh_token": "...",
      "expires_at": 1710000000.0,   # unix seconds, optional
      "id_token": "...",            # optional
      "extra": {...}                # provider-specific, optional
    }

This mirrors how 9router stores connection records without requiring a separate
oauth_tokens table.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

_REFRESH_MARGIN_S = 120.0


def parse_credential(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"access_token": raw}


def serialize_credential(cred: dict[str, Any]) -> str:
    return json.dumps(cred, separators=(",", ":"), ensure_ascii=False)


def access_token(cred: dict[str, Any]) -> str:
    for key in ("access_token", "accessToken", "token", "api_key"):
        val = cred.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def refresh_token(cred: dict[str, Any]) -> str:
    for key in ("refresh_token", "refreshToken"):
        val = cred.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def needs_refresh(cred: dict[str, Any], *, margin_s: float = _REFRESH_MARGIN_S) -> bool:
    if not access_token(cred):
        return bool(refresh_token(cred))
    exp = cred.get("expires_at") or cred.get("expiresAt")
    if exp is None:
        return False
    try:
        return time.time() >= float(exp) - margin_s
    except (TypeError, ValueError):
        return False


def apply_token_response(
    cred: dict[str, Any],
    tokens: dict[str, Any],
) -> dict[str, Any]:
    next_cred = dict(cred)
    at = tokens.get("access_token") or tokens.get("accessToken")
    if isinstance(at, str) and at:
        next_cred["access_token"] = at
    rt = tokens.get("refresh_token") or tokens.get("refreshToken")
    if isinstance(rt, str) and rt:
        next_cred["refresh_token"] = rt
    idt = tokens.get("id_token") or tokens.get("idToken")
    if isinstance(idt, str) and idt:
        next_cred["id_token"] = idt
    expires_in = tokens.get("expires_in") or tokens.get("expiresIn")
    if expires_in is not None:
        try:
            next_cred["expires_at"] = time.time() + float(expires_in)
        except (TypeError, ValueError):
            pass
    return next_cred


# Public OAuth client IDs (same as official CLIs / 9router)
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"

CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
CLAUDE_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
ANTIGRAVITY_CLIENT_ID = os.getenv("ANTIGRAVITY_CLIENT_ID", "")
ANTIGRAVITY_CLIENT_SECRET = os.getenv("ANTIGRAVITY_CLIENT_SECRET", "")
GOOGLE_CLI_CLIENT_ID = os.getenv("GOOGLE_CLI_CLIENT_ID", "")
GOOGLE_CLI_CLIENT_SECRET = os.getenv("GOOGLE_CLI_CLIENT_SECRET", "")

KIRO_SOCIAL_REFRESH_URL = "https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken"
KIRO_IDC_TOKEN_URL = "https://oidc.us-east-1.amazonaws.com/token"
KIRO_DEVICE_AUTH_URL = "https://oidc.us-east-1.amazonaws.com/device_authorization"
KIRO_REGISTER_CLIENT_URL = "https://oidc.us-east-1.amazonaws.com/client/register"


async def refresh_codex(refresh_tok: str, client: httpx.AsyncClient) -> dict[str, Any] | None:
    r = await client.post(
        CODEX_TOKEN_URL,
        json={
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if r.status_code >= 400:
        return None
    data = r.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return dict(data)


async def refresh_claude(refresh_tok: str, client: httpx.AsyncClient) -> dict[str, Any] | None:
    r = await client.post(
        CLAUDE_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": CLAUDE_CLIENT_ID,
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if r.status_code >= 400:
        return None
    data = r.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return dict(data)


async def refresh_google(
    refresh_tok: str,
    client: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
) -> dict[str, Any] | None:
    r = await client.post(
        GOOGLE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    if r.status_code >= 400:
        return None
    data = r.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return dict(data)


async def refresh_kiro_social(
    refresh_tok: str,
    client: httpx.AsyncClient,
) -> dict[str, Any] | None:
    r = await client.post(
        KIRO_SOCIAL_REFRESH_URL,
        json={"refreshToken": refresh_tok},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if r.status_code >= 400:
        return None
    data = r.json()
    if not isinstance(data, dict):
        return None
    # Kiro social returns accessToken / refreshToken camelCase
    at = data.get("accessToken") or data.get("access_token")
    if not at:
        return None
    return {
        "access_token": at,
        "refresh_token": data.get("refreshToken") or data.get("refresh_token") or refresh_tok,
        "expires_in": data.get("expiresIn") or data.get("expires_in"),
        "profileArn": data.get("profileArn"),
    }

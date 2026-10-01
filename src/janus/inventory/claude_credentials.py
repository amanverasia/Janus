"""Normalize Claude Code OAuth credentials for Key Inventory."""

from __future__ import annotations

import json
from typing import Any

from janus.providers.credential_persistence import credential_expiry


def _string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_claude_credential(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Empty Claude credential")
    if not text.startswith(("{", "[")):
        return json.dumps({"access_token": text})
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Claude credential JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Claude credential JSON must be an object")
    nested = data.get("claudeAiOauth")
    source = nested if isinstance(nested, dict) else data
    access = _string(source, "access_token", "accessToken")
    if not access:
        raise ValueError("Claude credential missing access token")
    out: dict[str, Any] = {"access_token": access}
    refresh = _string(source, "refresh_token", "refreshToken")
    if refresh:
        out["refresh_token"] = refresh
    expires = credential_expiry(source)
    if expires is not None:
        out["expires_at"] = expires
    subscription = _string(source, "subscriptionType", "subscription_type")
    if subscription:
        out["extra"] = {"subscriptionType": subscription}
    return json.dumps(out)

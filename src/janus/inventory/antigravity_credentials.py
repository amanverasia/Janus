"""Normalize Antigravity / Google Cloud Code OAuth credential blobs."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any


def _expires_at(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError as exc:
            raise ValueError(f"Invalid expiresAt: {value!r}") from exc
    raise ValueError(f"Invalid expiresAt type: {type(value).__name__}")


def normalize_antigravity_credential(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Empty Antigravity credential")
    if not text.startswith("{"):
        return json.dumps({"access_token": text}, separators=(",", ":"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Antigravity credential JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Antigravity credential JSON must be an object")

    def first(*keys: str) -> str:
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    access = first("access_token", "accessToken", "token")
    if not access:
        raise ValueError("Antigravity credential missing access token")
    refresh = first("refresh_token", "refreshToken")
    out: dict[str, Any] = {"access_token": access}
    if refresh:
        out["refresh_token"] = refresh
    expires = _expires_at(data.get("expires_at", data.get("expiresAt")))
    if expires is None and data.get("expires_in") is not None:
        try:
            expires = time.time() + float(data["expires_in"])
        except (TypeError, ValueError):
            raise ValueError("Invalid expiresIn") from None
    if expires is not None:
        out["expires_at"] = expires
    for key in ("id_token", "idToken"):
        if isinstance(data.get(key), str) and data[key]:
            out["id_token"] = data[key]
            break

    extra: dict[str, Any] = {}
    for source in (data.get("extra"), data.get("providerSpecificData"), data):
        if not isinstance(source, dict):
            continue
        project = source.get("projectId") or source.get("project_id")
        if isinstance(project, str) and project:
            extra["projectId"] = project
            break
    if extra:
        out["extra"] = extra
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False)

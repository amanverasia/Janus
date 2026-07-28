"""Normalize Codex / 9router OAuth credential pastes for Key Inventory."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _parse_expires_at(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            pass
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError as exc:
            raise ValueError(f"Invalid expiresAt: {value!r}") from exc
    raise ValueError(f"Invalid expiresAt type: {type(value).__name__}")


def _workspace_id(data: dict[str, Any]) -> str | None:
    extra = data.get("extra")
    if isinstance(extra, dict):
        wid = extra.get("workspaceId") or extra.get("chatgptAccountId")
        if isinstance(wid, str) and wid:
            return wid
    wid = data.get("workspaceId")
    if isinstance(wid, str) and wid:
        return wid
    psd = data.get("providerSpecificData")
    if isinstance(psd, dict):
        wid = psd.get("chatgptAccountId")
        if isinstance(wid, str) and wid:
            return wid
    return None


def _access(data: dict[str, Any]) -> str:
    for key in ("access_token", "accessToken", "token", "api_key"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _refresh(data: dict[str, Any]) -> str:
    for key in ("refresh_token", "refreshToken"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def normalize_codex_credential(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Empty Codex credential")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid Codex credential JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Codex credential JSON must be an object")
        at = _access(data)
        if not at:
            raise ValueError("Codex credential missing access token")
        out: dict[str, Any] = {"access_token": at}
        rt = _refresh(data)
        if rt:
            out["refresh_token"] = rt
        exp = data.get("expires_at", data.get("expiresAt"))
        parsed_exp = _parse_expires_at(exp)
        if parsed_exp is not None:
            out["expires_at"] = parsed_exp
        idt = data.get("id_token") or data.get("idToken")
        if isinstance(idt, str) and idt:
            out["id_token"] = idt
        wid = _workspace_id(data)
        if wid:
            out["extra"] = {"workspaceId": wid}
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    if text.startswith("["):
        raise ValueError("Use expand_codex_paste for arrays")
    return json.dumps({"access_token": text}, separators=(",", ":"), ensure_ascii=False)


def _label_for(conn: dict[str, Any]) -> str:
    for key in ("name", "email"):
        val = conn.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _is_codex_connection(conn: dict[str, Any]) -> bool:
    prov = conn.get("provider")
    if prov is None:
        return True
    return str(prov).lower() in {"codex", "openai-codex", "chatgpt"}


def expand_codex_paste(raw: str) -> list[dict[str, str]]:
    text = raw.strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON paste") from exc
        connections: list[Any]
        if isinstance(data, dict) and isinstance(data.get("providerConnections"), list):
            connections = data["providerConnections"]
        elif isinstance(data, list):
            connections = data
        elif isinstance(data, dict):
            return [
                {
                    "key": normalize_codex_credential(json.dumps(data)),
                    "label": _label_for(data),
                }
            ]
        else:
            raise ValueError("Unsupported Codex paste shape")
        entries: list[dict[str, str]] = []
        for item in connections:
            if not isinstance(item, dict):
                continue
            if not _is_codex_connection(item):
                continue
            if not _access(item) and not _refresh(item):
                continue
            entries.append(
                {
                    "key": normalize_codex_credential(json.dumps(item)),
                    "label": _label_for(item),
                }
            )
        return entries
    return [{"key": normalize_codex_credential(text), "label": ""}]

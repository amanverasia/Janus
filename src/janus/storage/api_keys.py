from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

from .database import get_connection
from .key_access import parse_allowed_models, serialize_allowed_models

_UNSET: Any = object()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _row_to_key(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["can_login"] = bool(data.get("can_login", 1))
    data["allowed_models"] = parse_allowed_models(data.get("allowed_models"))
    return data


async def create_key(
    db_path: str | Path,
    name: str,
    *,
    can_login: bool = True,
    allowed_models: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    raw = secrets.token_hex(16)
    key = f"sk-janus-{raw}"
    key_hash = _hash_key(key)
    prefix = key[:16]
    models_json = serialize_allowed_models(allowed_models)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO api_keys (name, key_hash, prefix, can_login, allowed_models) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, key_hash, prefix, 1 if can_login else 0, models_json),
        )
        await db.commit()
        record_id = cursor.lastrowid
    return key, {
        "id": record_id,
        "name": name,
        "prefix": prefix,
        "can_login": can_login,
        "allowed_models": allowed_models if allowed_models else None,
    }


async def verify_key(db_path: str | Path, key: str) -> int | None:
    if not key.startswith("sk-janus-"):
        return None
    key_hash = _hash_key(key)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return int(row["id"])


async def get_key_policy(db_path: str | Path, key_id: int) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, can_login, allowed_models FROM api_keys WHERE id = ? AND is_active = 1",
            (key_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "can_login": bool(row["can_login"]),
        "allowed_models": parse_allowed_models(row["allowed_models"]),
    }


async def update_key(
    db_path: str | Path,
    key_id: int,
    *,
    name: str | None = None,
    can_login: bool | None = None,
    allowed_models: Any = _UNSET,
) -> bool:
    updates: list[str] = []
    params: list[Any] = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if can_login is not None:
        updates.append("can_login = ?")
        params.append(1 if can_login else 0)
    if allowed_models is not _UNSET:
        updates.append("allowed_models = ?")
        if allowed_models is None:
            params.append(None)
        else:
            params.append(serialize_allowed_models(list(allowed_models)))
    if not updates:
        return False
    params.append(key_id)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"UPDATE api_keys SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_keys(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, name, prefix, is_active, can_login, allowed_models, created_at "
            "FROM api_keys ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_key(row) for row in rows]


async def revoke_key(db_path: str | Path, key_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
        await db.commit()

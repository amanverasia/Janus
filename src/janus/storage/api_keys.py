from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

from .database import get_connection


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def create_key(db_path: str | Path, name: str) -> tuple[str, dict[str, Any]]:
    raw = secrets.token_hex(16)
    key = f"sk-janus-{raw}"
    key_hash = _hash_key(key)
    prefix = key[:16]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO api_keys (name, key_hash, prefix) VALUES (?, ?, ?)",
            (name, key_hash, prefix),
        )
        await db.commit()
        record_id = cursor.lastrowid
    return key, {"id": record_id, "name": name, "prefix": prefix}


async def verify_key(db_path: str | Path, key: str) -> bool:
    if not key.startswith("sk-janus-"):
        return False
    key_hash = _hash_key(key)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ? AND is_active = 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def list_keys(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, name, prefix, is_active, created_at FROM api_keys ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def revoke_key(db_path: str | Path, key_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
        await db.commit()

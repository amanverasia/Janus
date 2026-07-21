from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from janus.inventory.key_encryption import (
    decrypt_key_value,
    encrypt_key_value,
    encryption_enabled,
    is_encrypted_value,
)

from .database import get_connection


def _stored_api_key(value: Any) -> Any:
    if isinstance(value, str) and value:
        return encrypt_key_value(value)
    return value


def _decode_provider_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    api_key = item.get("api_key")
    if isinstance(api_key, str) and api_key:
        item["api_key"] = decrypt_key_value(api_key)
    return item


async def list_providers(db_path: str | Path, enabled_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM providers"
    if enabled_only:
        query += " WHERE is_enabled = 1"
    query += " ORDER BY id"
    async with get_connection(db_path) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
    return [_decode_provider_row(row) for row in rows]


async def get_provider(db_path: str | Path, provider_id: str) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)) as cur:
            row = await cur.fetchone()
    return _decode_provider_row(row) if row else None


async def create_provider(db_path: str | Path, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO providers
               (id, prefix, api_type, base_url, api_key, models,
                quota_window, quota_limit, quota_metric, transports, allowed_models)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data["prefix"],
                data["api_type"],
                data["base_url"],
                _stored_api_key(data.get("api_key")),
                json.dumps(data.get("models", [])),
                data.get("quota_window"),
                data.get("quota_limit"),
                data.get("quota_metric") or "requests",
                json.dumps(data.get("transports")) if data.get("transports") else None,
                json.dumps(data.get("allowed_models", [])),
            ),
        )
        await db.commit()


async def update_provider(db_path: str | Path, provider_id: str, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """UPDATE providers SET prefix = ?, api_type = ?, base_url = ?,
               api_key = ?, models = ?, quota_window = ?, quota_limit = ?,
               quota_metric = ?, transports = ?, allowed_models = ?,
               updated_at = datetime('now')
               WHERE id = ?""",
            (
                data["prefix"],
                data["api_type"],
                data["base_url"],
                _stored_api_key(data.get("api_key")),
                json.dumps(data.get("models", [])),
                data.get("quota_window"),
                data.get("quota_limit"),
                data.get("quota_metric") or "requests",
                json.dumps(data.get("transports")) if data.get("transports") else None,
                json.dumps(data.get("allowed_models", [])),
                provider_id,
            ),
        )
        await db.commit()


async def reencrypt_plaintext_provider_keys(db_path: str | Path) -> int:
    if not encryption_enabled():
        raise RuntimeError("INVENTORY_ENCRYPTION_KEY must be set to encrypt provider credentials")
    converted = 0
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, api_key FROM providers WHERE api_key IS NOT NULL AND api_key != ''"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            stored = row["api_key"]
            if not isinstance(stored, str) or is_encrypted_value(stored):
                continue
            await db.execute(
                "UPDATE providers SET api_key = ?, updated_at = datetime('now') WHERE id = ?",
                (encrypt_key_value(stored), row["id"]),
            )
            converted += 1
        await db.commit()
    return converted


async def count_provider_encryption_state(db_path: str | Path) -> dict[str, int]:
    encrypted = 0
    plaintext = 0
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT api_key FROM providers WHERE api_key IS NOT NULL AND api_key != ''"
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        stored = row["api_key"]
        if isinstance(stored, str) and is_encrypted_value(stored):
            encrypted += 1
        else:
            plaintext += 1
    return {"encrypted": encrypted, "plaintext": plaintext, "total": encrypted + plaintext}


async def toggle_provider(db_path: str | Path, provider_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE providers SET is_enabled = 1 - is_enabled,"
            " updated_at = datetime('now') WHERE id = ?",
            (provider_id,),
        )
        await db.commit()


async def delete_provider(db_path: str | Path, provider_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
        await db.commit()

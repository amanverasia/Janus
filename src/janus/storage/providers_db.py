from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import get_connection


async def list_providers(db_path: str | Path, enabled_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM providers"
    if enabled_only:
        query += " WHERE is_enabled = 1"
    query += " ORDER BY id"
    async with get_connection(db_path) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_provider(db_path: str | Path, provider_id: str) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


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
                data.get("api_key"),
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
                data.get("api_key"),
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

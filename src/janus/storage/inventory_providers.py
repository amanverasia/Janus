from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def list_inventory_providers(
    db_path: str | Path,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM inventory_providers"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY display_name"
    async with get_connection(db_path) as db:
        async with db.execute(query) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_inventory_provider(
    db_path: str | Path,
    provider_id: str,
) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM inventory_providers WHERE id = ?",
            (provider_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import get_connection


async def list_combos(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM combos ORDER BY name") as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_combo(db_path: str | Path, combo_id: int) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM combos WHERE id = ?", (combo_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def create_combo(db_path: str | Path, data: dict[str, Any]) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO combos (name, models) VALUES (?, ?)",
            (data["name"], json.dumps(data["models"])),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_combo(db_path: str | Path, combo_id: int, data: dict[str, Any]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE combos SET name = ?, models = ?, updated_at = datetime('now') WHERE id = ?",
            (data["name"], json.dumps(data["models"]), combo_id),
        )
        await db.commit()


async def delete_combo(db_path: str | Path, combo_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM combos WHERE id = ?", (combo_id,))
        await db.commit()

from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def list_pricing_overrides(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM pricing_overrides ORDER BY model") as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_pricing_overrides(db_path: str | Path) -> dict[str, dict[str, float]]:
    rows = await list_pricing_overrides(db_path)
    return {
        row["model"]: {
            "input_per_mtok": row["input_per_mtok"],
            "output_per_mtok": row["output_per_mtok"],
            "cache_creation_per_mtok": row["cache_creation_per_mtok"],
            "cache_read_per_mtok": row["cache_read_per_mtok"],
        }
        for row in rows
    }


async def create_or_update_pricing_override(
    db_path: str | Path, data: dict[str, float | str]
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO pricing_overrides
               (model, input_per_mtok, output_per_mtok,
                cache_creation_per_mtok, cache_read_per_mtok)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(model) DO UPDATE SET
               input_per_mtok = excluded.input_per_mtok,
               output_per_mtok = excluded.output_per_mtok,
               cache_creation_per_mtok = excluded.cache_creation_per_mtok,
               cache_read_per_mtok = excluded.cache_read_per_mtok""",
            (
                str(data["model"]),
                float(data["input_per_mtok"]),
                float(data["output_per_mtok"]),
                float(data.get("cache_creation_per_mtok", 0.0)),
                float(data.get("cache_read_per_mtok", 0.0)),
            ),
        )
        await db.commit()


async def delete_pricing_override(db_path: str | Path, model: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM pricing_overrides WHERE model = ?", (model,))
        await db.commit()

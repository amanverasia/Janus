"""Live-synced pricing catalog storage (LiteLLM + OpenRouter sourced).

This is distinct from ``pricing_overrides`` (user-entered manual overrides).
The catalog is fully replaced on each sync — see ``janus.pricing.sync``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def list_catalog(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM pricing_catalog ORDER BY model") as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_catalog(db_path: str | Path) -> dict[str, dict[str, float]]:
    rows = await list_catalog(db_path)
    return {
        row["model"]: {
            "input_per_mtok": row["input_per_mtok"],
            "output_per_mtok": row["output_per_mtok"],
            "cache_creation_per_mtok": row["cache_creation_per_mtok"],
            "cache_read_per_mtok": row["cache_read_per_mtok"],
        }
        for row in rows
    }


async def catalog_count(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM pricing_catalog") as cur:
            row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def replace_catalog(db_path: str | Path, rows: list[dict[str, Any]]) -> int:
    """Atomically replace the entire pricing_catalog with ``rows``.

    Each row dict must have keys: model, input_per_mtok, output_per_mtok,
    cache_creation_per_mtok, cache_read_per_mtok, source. Runs DELETE + INSERT
    inside a single transaction so readers never see a partially-replaced table.
    Returns the number of rows written.
    """
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM pricing_catalog")
        if rows:
            await db.executemany(
                """INSERT INTO pricing_catalog
                   (model, input_per_mtok, output_per_mtok,
                    cache_creation_per_mtok, cache_read_per_mtok, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row["model"],
                        row["input_per_mtok"],
                        row["output_per_mtok"],
                        row.get("cache_creation_per_mtok", 0.0),
                        row.get("cache_read_per_mtok", 0.0),
                        row["source"],
                    )
                    for row in rows
                ],
            )
        await db.commit()
    return len(rows)

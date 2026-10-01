from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

_OVERRIDE_CACHE_TTL_S = 30.0
_cache: dict[str, tuple[float, dict[str, float]]] = {}


async def ensure_override_tables(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS model_quality_overrides (
                   model TEXT PRIMARY KEY,
                   quality REAL NOT NULL,
                   note TEXT,
                   updated_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        await db.commit()


async def upsert_override(
    db_path: str | Path, model: str, quality: float, note: str | None = None
) -> dict[str, Any]:
    model = model.strip()
    if not model:
        raise ValueError("model must not be empty")
    if not 0.0 <= quality <= 1.0:
        raise ValueError("quality must be between 0.0 and 1.0")
    await ensure_override_tables(db_path)
    updated_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(sep=" ")
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO model_quality_overrides (model, quality, note, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(model) DO UPDATE SET
                   quality = excluded.quality,
                   note = excluded.note,
                   updated_at = excluded.updated_at""",
            (model, quality, note, updated_at),
        )
        await db.commit()
    _cache.pop(str(db_path), None)
    return {"model": model, "quality": quality, "note": note, "updated_at": updated_at}


async def delete_override(db_path: str | Path, model: str) -> bool:
    await ensure_override_tables(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute("DELETE FROM model_quality_overrides WHERE model = ?", (model,))
        await db.commit()
    _cache.pop(str(db_path), None)
    return bool(cursor.rowcount)


async def list_overrides(db_path: str | Path) -> list[dict[str, Any]]:
    await ensure_override_tables(db_path)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT model, quality, note, updated_at FROM model_quality_overrides ORDER BY model"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_override_map(db_path: str | Path) -> dict[str, float]:
    import time

    key = str(db_path)
    cached = _cache.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return dict(cached[1])
    rows = await list_overrides(db_path)
    mapping = {str(row["model"]): float(row["quality"]) for row in rows}
    _cache[key] = (time.monotonic() + _OVERRIDE_CACHE_TTL_S, mapping)
    return mapping


def invalidate_override_cache(db_path: str | Path | None = None) -> None:
    if db_path is None:
        _cache.clear()
        return
    _cache.pop(str(db_path), None)

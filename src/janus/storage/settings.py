from __future__ import annotations

from pathlib import Path

from .database import get_connection


async def get_setting(db_path: str | Path, key: str, default: str | None = None) -> str | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(db_path: str | Path, key: str, value: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_all_settings(db_path: str | Path) -> dict[str, str]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
    return {row["key"]: row["value"] for row in rows}

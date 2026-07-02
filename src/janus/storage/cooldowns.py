from __future__ import annotations

import time
from pathlib import Path

from .database import get_connection


async def save_cooldown(db_path: str | Path, account_id: str, expires_at: float) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO cooldowns (account_id, expires_at) VALUES (?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET expires_at = excluded.expires_at",
            (account_id, expires_at),
        )
        await db.commit()


async def get_active_cooldowns(db_path: str | Path) -> dict[str, float]:
    now = time.time()
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM cooldowns WHERE expires_at <= ?", (now,))
        await db.commit()
        async with db.execute(
            "SELECT account_id, expires_at FROM cooldowns WHERE expires_at > ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return {row["account_id"]: row["expires_at"] for row in rows}

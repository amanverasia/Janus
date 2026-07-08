from __future__ import annotations

import time
from pathlib import Path

from .database import get_connection


async def save_cooldown(
    db_path: str | Path,
    account_id: str,
    expires_at: float,
    model: str = "__all__",
    error_type: str | None = None,
    backoff_level: int = 0,
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO cooldowns (account_id, model, expires_at, error_type, backoff_level) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, model) DO UPDATE SET "
            "expires_at = excluded.expires_at, error_type = excluded.error_type, "
            "backoff_level = excluded.backoff_level",
            (account_id, model, expires_at, error_type, backoff_level),
        )
        await db.commit()


async def get_active_cooldowns(db_path: str | Path) -> dict[str, tuple[float, int]]:
    now = time.time()
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM cooldowns WHERE expires_at <= ?", (now,))
        await db.commit()
        async with db.execute(
            "SELECT account_id, model, expires_at, backoff_level FROM cooldowns"
        ) as cur:
            rows = await cur.fetchall()
    return {
        f"{row['account_id']}::{row['model']}": (row["expires_at"], row["backoff_level"])
        for row in rows
    }

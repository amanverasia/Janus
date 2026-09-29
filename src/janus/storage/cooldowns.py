from __future__ import annotations

import logging
import time
from pathlib import Path

from .database import get_connection

logger = logging.getLogger(__name__)

_EXPIRED_PRUNE_MIN_INTERVAL_S = 300.0
_last_expired_prune: dict[str, float] = {}


async def prune_expired_cooldowns(db_path: str | Path) -> int:
    now = time.time()
    async with get_connection(db_path) as db:
        cur = await db.execute("DELETE FROM cooldowns WHERE expires_at <= ?", (now,))
        await db.commit()
        return int(cur.rowcount or 0)


async def _maybe_prune_expired(db_path: str | Path) -> None:
    key = str(db_path)
    now = time.monotonic()
    last = _last_expired_prune.get(key)
    if last is not None and now - last < _EXPIRED_PRUNE_MIN_INTERVAL_S:
        return
    _last_expired_prune[key] = now
    try:
        await prune_expired_cooldowns(db_path)
    except Exception as e:
        logger.warning("Failed to prune expired cooldowns: %s", e)


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
    await _maybe_prune_expired(db_path)


async def delete_cooldown(db_path: str | Path, account_id: str, model: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM cooldowns WHERE account_id = ? AND model = ?",
            (account_id, model),
        )
        await db.commit()
    await _maybe_prune_expired(db_path)


async def clear_all_cooldowns(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute("DELETE FROM cooldowns")
        await db.commit()
        return int(cur.rowcount or 0)


async def get_active_cooldowns(db_path: str | Path) -> dict[str, tuple[float, int]]:
    now = time.time()
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT account_id, model, expires_at, backoff_level "
            "FROM cooldowns WHERE expires_at > ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return {
        f"{row['account_id']}::{row['model']}": (row["expires_at"], row["backoff_level"])
        for row in rows
    }

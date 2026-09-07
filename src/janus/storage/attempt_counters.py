from __future__ import annotations

import logging
from pathlib import Path

from .database import get_connection

logger = logging.getLogger(__name__)

DAILY_SCOPE = "daily"
QUOTA_REQUESTS_SCOPE = "quota_requests"
QUOTA_TOKENS_SCOPE = "quota_tokens"

# All quota windows span at most a calendar month, so a counter row untouched
# for this many days belongs to an expired window and can be dropped.
PRUNE_DAYS = 35


async def bump_attempt_counter(
    db_path: str | Path,
    scope: str,
    scope_key: str,
    window_id: str,
    amount: int,
) -> None:
    """Add ``amount`` to a persisted window counter (fire-and-forget safe)."""
    if amount <= 0:
        return
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO attempt_counters (scope, scope_key, window_id, count)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(scope, scope_key, window_id) DO UPDATE SET
                   count = count + excluded.count, updated_at = datetime('now')""",
                (scope, scope_key, window_id, amount),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to persist attempt counter %s/%s: %s", scope, scope_key, e)


async def get_attempt_counts(
    db_path: str | Path,
    scope: str,
    window_id: str,
) -> dict[str, int]:
    """Persisted counts for one scope in the given window, keyed by scope key."""
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT scope_key, count FROM attempt_counters WHERE scope = ? AND window_id = ?",
            (scope, window_id),
        ) as cur:
            rows = await cur.fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


async def prune_attempt_counters(db_path: str | Path, days: int = PRUNE_DAYS) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                "DELETE FROM attempt_counters WHERE updated_at < datetime('now', ?)",
                (f"-{days} days",),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to prune attempt counters: %s", e)

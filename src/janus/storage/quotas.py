from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .database import get_connection

QUOTA_WINDOWS = ("5h", "daily", "weekly", "monthly")

_FIVE_HOURS_S = 5 * 3600


def window_id(window: str, now: datetime | None = None) -> str:
    """Stable identifier for the current quota window (UTC)."""
    now = now or datetime.now(UTC)
    if window == "5h":
        return str(int(now.timestamp()) // _FIVE_HOURS_S)
    if window == "daily":
        return now.strftime("%Y-%m-%d")
    if window == "weekly":
        return now.strftime("%G-W%V")
    if window == "monthly":
        return now.strftime("%Y-%m")
    raise ValueError(f"Unknown quota window: {window}")


def window_start(window: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    if window == "5h":
        bucket = int(now.timestamp()) // _FIVE_HOURS_S
        return datetime.fromtimestamp(bucket * _FIVE_HOURS_S, tz=UTC)
    if window == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "weekly":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=now.weekday())
    if window == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unknown quota window: {window}")


def window_reset(window: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    start = window_start(window, now)
    if window == "5h":
        return start + timedelta(seconds=_FIVE_HOURS_S)
    if window == "daily":
        return start + timedelta(days=1)
    if window == "weekly":
        return start + timedelta(days=7)
    if window == "monthly":
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    raise ValueError(f"Unknown quota window: {window}")


async def get_window_usage(
    db_path: str | Path,
    provider_row_id: str,
    window: str,
) -> dict[str, int]:
    """Requests and tokens consumed by a provider row in the current window.

    Matches both the bare provider id and inventory-expanded ids
    (`{row_id}::uk_{key}`), since quota applies to the whole subscription.
    """
    start = window_start(window).strftime("%Y-%m-%d %H:%M:%S")
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT COUNT(*) AS requests,
                      COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens
               FROM usage
               WHERE (provider_id = ? OR provider_id LIKE ? || '::%')
                 AND timestamp >= ?""",
            (provider_row_id, provider_row_id, start),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return {"requests": 0, "tokens": 0}
    return {"requests": int(row["requests"]), "tokens": int(row["tokens"])}


def describe_reset(window: str, now: datetime | None = None) -> dict[str, Any]:
    """Reset timestamp + human countdown for dashboard display."""
    now = now or datetime.now(UTC)
    reset = window_reset(window, now)
    remaining = max(int((reset - now).total_seconds()), 0)
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    if hours >= 48:
        countdown = f"{hours // 24}d {hours % 24}h"
    elif hours > 0:
        countdown = f"{hours}h {minutes}m"
    else:
        countdown = f"{minutes}m"
    return {"resets_at": reset.isoformat(), "resets_in": countdown}

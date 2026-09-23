from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .database import get_connection
from .time_windows import current_reporting_day


class _Unset(Enum):
    VALUE = "unset"


async def create_or_update_budget(
    db_path: str | Path,
    *,
    key_id: int | None,
    daily_limit: float | None | _Unset = _Unset.VALUE,
    absolute_limit: float | None | _Unset = _Unset.VALUE,
    warn_pct: float | None = None,
) -> int | None:
    for label, limit in (("Daily", daily_limit), ("Absolute", absolute_limit)):
        if not isinstance(limit, _Unset) and limit is not None:
            if not math.isfinite(limit) or limit < 0:
                raise ValueError(f"{label} limit must be a finite non-negative number.")
    if warn_pct is not None and (not math.isfinite(warn_pct) or not 1 <= warn_pct <= 100):
        raise ValueError("Warning percentage must be between 1 and 100.")
    if key_id is None and absolute_limit is not None and not isinstance(absolute_limit, _Unset):
        raise ValueError("Absolute budgets require a specific API key.")

    async with get_connection(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        if key_id is not None:
            async with db.execute("SELECT id FROM api_keys WHERE id = ?", (key_id,)) as cur:
                if await cur.fetchone() is None:
                    raise ValueError("The selected API key does not exist.")
        async with db.execute(
            "SELECT id, daily_limit, absolute_limit, warn_pct FROM budgets "
            "WHERE key_id IS ? AND is_active = 1 ORDER BY id LIMIT 1",
            (key_id,),
        ) as cur:
            row = await cur.fetchone()
        if isinstance(daily_limit, _Unset):
            daily_limit = row["daily_limit"] if row is not None else None
        if isinstance(absolute_limit, _Unset):
            absolute_limit = row["absolute_limit"] if row is not None else None
        if warn_pct is None:
            warn_pct = float(row["warn_pct"]) if row is not None else 80.0
        if daily_limit is None and absolute_limit is None:
            if row is not None:
                await db.execute("DELETE FROM budgets WHERE id = ?", (row["id"],))
            await db.commit()
            return None
        if row is not None:
            await db.execute(
                "UPDATE budgets SET daily_limit = ?, absolute_limit = ?, warn_pct = ? WHERE id = ?",
                (daily_limit, absolute_limit, warn_pct, row["id"]),
            )
            await db.commit()
            return int(row["id"])
        cursor = await db.execute(
            "INSERT INTO budgets (key_id, daily_limit, absolute_limit, warn_pct) "
            "VALUES (?, ?, ?, ?)",
            (key_id, daily_limit, absolute_limit, warn_pct),
        )
        await db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid


async def get_budgets(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, key_id, daily_limit, absolute_limit, warn_pct, is_active, created_at "
            "FROM budgets WHERE is_active = 1 ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_budget(db_path: str | Path, budget_id: int) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
        await db.commit()
        return cursor.rowcount > 0


def _limit_status(pct_used: float, warn_pct: float) -> str:
    if pct_used >= 100:
        return "exceeded"
    if pct_used >= warn_pct:
        return "warning"
    return "ok"


async def get_budget_status(
    db_path: str | Path,
    *,
    key_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    window = await current_reporting_day(db_path, now=now)
    start_utc, end_utc = window.query_bounds
    total_spend: float | None = None
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT daily_limit, absolute_limit, warn_pct FROM budgets "
            "WHERE key_id IS ? AND is_active = 1 ORDER BY id LIMIT 1",
            (key_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        if key_id is not None:
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage "
                "WHERE client_key_id = ? AND timestamp >= ? AND timestamp < ?",
                (key_id, start_utc, end_utc),
            ) as cur:
                spent_row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage "
                "WHERE timestamp >= ? AND timestamp < ?",
                (start_utc, end_utc),
            ) as cur:
                spent_row = await cur.fetchone()
        if row["absolute_limit"] is not None:
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage WHERE client_key_id = ?",
                (key_id,),
            ) as cur:
                total_row = await cur.fetchone()
            assert total_row is not None
            total_spend = float(total_row["spent"])

    assert spent_row is not None
    daily_limit = float(row["daily_limit"]) if row["daily_limit"] is not None else None
    absolute_limit = float(row["absolute_limit"]) if row["absolute_limit"] is not None else None
    warn_pct = float(row["warn_pct"])
    today_spend = float(spent_row["spent"])
    pct_used = 0.0
    daily_status: str | None = None
    if daily_limit is not None:
        pct_used = (today_spend / daily_limit * 100) if daily_limit > 0 else 100.0
        daily_status = _limit_status(pct_used, warn_pct)
    absolute_pct_used: float | None = None
    absolute_status: str | None = None
    absolute_remaining: float | None = None
    if absolute_limit is not None and total_spend is not None:
        absolute_pct_used = (total_spend / absolute_limit * 100) if absolute_limit > 0 else 100.0
        absolute_status = _limit_status(absolute_pct_used, warn_pct)
        absolute_remaining = max(0.0, absolute_limit - total_spend)
    statuses = (daily_status, absolute_status)
    status = "exceeded" if "exceeded" in statuses else "warning" if "warning" in statuses else "ok"
    resets_daily = daily_limit is not None and absolute_status != "exceeded"
    return {
        "daily_limit": daily_limit,
        "today_spend": today_spend,
        "remaining": max(0.0, daily_limit - today_spend) if daily_limit is not None else None,
        "pct_used": pct_used,
        "daily_status": daily_status,
        "absolute_limit": absolute_limit,
        "total_spend": total_spend,
        "absolute_remaining": absolute_remaining,
        "absolute_pct_used": absolute_pct_used,
        "absolute_status": absolute_status,
        "status": status,
        "warn_pct": warn_pct,
        "reporting_timezone": window.timezone,
        "retry_after": window.retry_after_seconds if resets_daily else None,
        "resets_at": window.end_utc.isoformat() if resets_daily else None,
    }

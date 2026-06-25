from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def create_or_update_budget(
    db_path: str | Path,
    *,
    key_id: int | None,
    daily_limit: float,
    warn_pct: float = 80,
) -> int:
    async with get_connection(db_path) as db:
        if key_id is not None:
            async with db.execute(
                "SELECT id FROM budgets WHERE key_id = ? AND is_active = 1",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT id FROM budgets WHERE key_id IS NULL AND is_active = 1"
            ) as cur:
                row = await cur.fetchone()
        if row is not None:
            await db.execute(
                "UPDATE budgets SET daily_limit = ?, warn_pct = ? WHERE id = ?",
                (daily_limit, warn_pct, row["id"]),
            )
            await db.commit()
            return int(row["id"])
        cursor = await db.execute(
            "INSERT INTO budgets (key_id, daily_limit, warn_pct) VALUES (?, ?, ?)",
            (key_id, daily_limit, warn_pct),
        )
        await db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid


async def get_budgets(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT id, key_id, daily_limit, warn_pct, is_active, created_at "
            "FROM budgets WHERE is_active = 1 ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_budget(db_path: str | Path, budget_id: int) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_budget_status(
    db_path: str | Path, *, key_id: int | None = None
) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        if key_id is not None:
            async with db.execute(
                "SELECT daily_limit, warn_pct FROM budgets WHERE key_id = ? AND is_active = 1",
                (key_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage "
                "WHERE client_key_id = ? AND date(timestamp) = date('now', 'localtime')",
                (key_id,),
            ) as cur:
                spent_row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT daily_limit, warn_pct FROM budgets WHERE key_id IS NULL AND is_active = 1"
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            async with db.execute(
                "SELECT COALESCE(SUM(cost), 0.0) as spent FROM usage "
                "WHERE date(timestamp) = date('now', 'localtime')"
            ) as cur:
                spent_row = await cur.fetchone()

    assert row is not None
    assert spent_row is not None
    daily_limit = float(row["daily_limit"])
    warn_pct = float(row["warn_pct"])
    today_spend = float(spent_row["spent"])
    pct_used = (today_spend / daily_limit * 100) if daily_limit > 0 else 100.0
    if pct_used >= 100:
        status = "exceeded"
    elif pct_used >= warn_pct:
        status = "warning"
    else:
        status = "ok"
    return {
        "daily_limit": daily_limit,
        "today_spend": today_spend,
        "remaining": max(0.0, daily_limit - today_spend),
        "pct_used": pct_used,
        "status": status,
        "warn_pct": warn_pct,
    }

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .database import get_connection

Dimension = Literal["model", "provider", "account", "client_key"]

_DIMENSION_COLUMN = {
    "model": "model",
    "provider": "provider_id",
    "account": "account_id",
    "client_key": "client_key_id",
}


async def get_spend_summary(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT COUNT(*) as cnt,
                      COALESCE(SUM(input_tokens), 0) as inp,
                      COALESCE(SUM(output_tokens), 0) as outp,
                      COALESCE(SUM(cache_creation_tokens), 0) as cc,
                      COALESCE(SUM(cache_read_tokens), 0) as cr,
                      COALESCE(SUM(cost), 0.0) as cost
               FROM usage
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{days} days",),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None

        async with db.execute(
            """SELECT date(timestamp) as date,
                      COUNT(*) as requests,
                      COALESCE(SUM(cost), 0.0) as cost
               FROM usage
               WHERE timestamp >= datetime('now', ?)
               GROUP BY date(timestamp)
               ORDER BY date(timestamp)""",
            (f"-{days} days",),
        ) as cur:
            daily_rows = await cur.fetchall()

    return {
        "total_cost": row["cost"],
        "total_requests": row["cnt"],
        "total_input_tokens": row["inp"],
        "total_output_tokens": row["outp"],
        "total_cache_creation_tokens": row["cc"],
        "total_cache_read_tokens": row["cr"],
        "daily": [dict(r) for r in daily_rows],
    }


async def get_breakdown(
    db_path: str | Path, *, dimension: Dimension, days: int = 30
) -> list[dict[str, Any]]:
    col = _DIMENSION_COLUMN[dimension]
    async with get_connection(db_path) as db:
        async with db.execute(
            f"""SELECT {col} as {dimension},
                       COUNT(*) as requests,
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                       COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                       COALESCE(SUM(cost), 0.0) as cost
                FROM usage
                WHERE timestamp >= datetime('now', ?)
                GROUP BY {col}
                ORDER BY cost DESC""",
            (f"-{days} days",),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_success_rate(db_path: str | Path, *, days: int = 30) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                SUM(CASE WHEN status >= 200 AND status < 300 THEN 1 ELSE 0 END) as s2xx,
                SUM(CASE WHEN status >= 400 AND status < 500 THEN 1 ELSE 0 END) as s4xx,
                SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) as s5xx,
                COUNT(*) as total
               FROM usage
               WHERE timestamp >= datetime('now', ?)""",
            (f"-{days} days",),
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
    return {
        "success_2xx": row["s2xx"] or 0,
        "client_4xx": row["s4xx"] or 0,
        "server_5xx": row["s5xx"] or 0,
        "total": row["total"] or 0,
    }

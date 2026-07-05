from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)


async def record_usage(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    model: str | None = None,
    account_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    status: int = 0,
    client_key_id: int | None = None,
    client_key_label: str | None = None,
    cost: float = 0.0,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage
                   (provider_id, model, account_id, input_tokens, output_tokens,
                    cache_creation_tokens, cache_read_tokens, status, client_key_id,
                    client_key_label, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    provider_id,
                    model,
                    account_id,
                    input_tokens,
                    output_tokens,
                    cache_creation_tokens,
                    cache_read_tokens,
                    status,
                    client_key_id,
                    client_key_label,
                    cost,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record usage: %s", e)


async def get_request_counts_today(db_path: str | Path) -> dict[str, int]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT account_id, COUNT(*) FROM usage
               WHERE date(timestamp) = date('now') AND account_id IS NOT NULL
               GROUP BY account_id"""
        ) as cur:
            rows = await cur.fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


async def get_usage_stats(db_path: str | Path) -> dict[str, Any]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(input_tokens),0) as inp,"
            "COALESCE(SUM(output_tokens),0) as outp FROM usage"
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
        total_requests = row["cnt"]
        total_input = row["inp"]
        total_output = row["outp"]

        async with db.execute(
            """SELECT model, COUNT(*) as requests,
                      COALESCE(SUM(input_tokens),0) as input_tokens,
                      COALESCE(SUM(output_tokens),0) as output_tokens
               FROM usage GROUP BY model ORDER BY requests DESC"""
        ) as cur:
            model_rows = await cur.fetchall()

    return {
        "total_requests": total_requests,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "by_model": [dict(r) for r in model_rows],
    }

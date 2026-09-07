from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)


async def record_request_outcome(
    db_path: str | Path,
    *,
    client_format: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
    account_id: str | None = None,
    status: int,
    duration_ms: int | None = None,
    streamed: bool = False,
    attempts: int = 1,
    client_key_id: int | None = None,
    client_key_label: str | None = None,
) -> None:
    """Persist the terminal outcome of one client request.

    Exactly one row per client request, recorded on every terminal path
    (success, upstream error, exhausted fallback, budget block, parse error,
    interrupted stream) and independent of the request-logging setting, so
    analytics success rates are truthful. Intermediate fallback attempts are
    folded into the ``attempts`` counter, never extra rows.
    """
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO request_outcomes
                   (client_format, model, provider_id, account_id, status,
                    duration_ms, streamed, attempts, client_key_id, client_key_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    client_format,
                    model,
                    provider_id,
                    account_id,
                    status,
                    duration_ms,
                    1 if streamed else 0,
                    max(attempts, 1),
                    client_key_id,
                    client_key_label,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record request outcome: %s", e)


async def list_request_outcomes(
    db_path: str | Path,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT timestamp, client_format, model, provider_id, account_id,
                      status, duration_ms, streamed, attempts, client_key_id,
                      client_key_label
               FROM request_outcomes
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]

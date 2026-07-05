from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 64 * 1024
MAX_ROWS = 500


def _truncate(body: str | None) -> str | None:
    if body is None or len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS] + "\n…[truncated]"


async def record_request_log(
    db_path: str | Path,
    *,
    client_format: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
    account_id: str | None = None,
    status: int | None = None,
    duration_ms: int | None = None,
    streamed: bool = False,
    request_body: str | None = None,
    response_body: str | None = None,
    error: str | None = None,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO request_logs
                   (client_format, model, provider_id, account_id, status,
                    duration_ms, streamed, request_body, response_body, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    client_format,
                    model,
                    provider_id,
                    account_id,
                    status,
                    duration_ms,
                    1 if streamed else 0,
                    _truncate(request_body),
                    _truncate(response_body),
                    error,
                ),
            )
            await db.execute(
                "DELETE FROM request_logs WHERE id NOT IN "
                "(SELECT id FROM request_logs ORDER BY id DESC LIMIT ?)",
                (MAX_ROWS,),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record request log: %s", e)


async def list_request_logs(
    db_path: str | Path,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT id, timestamp, client_format, model, provider_id, account_id,
                      status, duration_ms, streamed, error
               FROM request_logs ORDER BY id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_request_log(db_path: str | Path, log_id: int) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM request_logs WHERE id = ?", (log_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def export_request_logs(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM request_logs ORDER BY id DESC") as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def count_request_logs(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT COUNT(*) AS n FROM request_logs") as cur:
            row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def clear_request_logs(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM request_logs")
        await db.commit()

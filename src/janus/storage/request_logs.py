from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 64 * 1024
MAX_ROWS = 500
FAILED_ATTEMPT_OUTCOMES = ("error", "client_error")
_FAILED_ATTEMPT_PREDICATE = (
    "EXISTS (SELECT 1 FROM attempt_signals s WHERE s.request_id = r.request_id "
    "AND s.outcome IN ('error', 'client_error'))"
)


def _truncate(body: str | None) -> str | None:
    if body is None or len(body) <= MAX_BODY_CHARS:
        return body
    return body[:MAX_BODY_CHARS] + "\n…[truncated]"


async def record_request_log(
    db_path: str | Path,
    *,
    request_id: str | None = None,
    client_format: str | None = None,
    model: str | None = None,
    resolved_model: str | None = None,
    provider_id: str | None = None,
    account_id: str | None = None,
    status: int | None = None,
    duration_ms: int | None = None,
    streamed: bool = False,
    request_body: str | None = None,
    response_body: str | None = None,
    error: str | None = None,
    client_key_id: int | None = None,
    client_key_label: str | None = None,
    max_rows: int | None = None,
) -> None:
    rows = max_rows if max_rows is not None else MAX_ROWS
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO request_logs
                   (request_id, client_format, model, resolved_model, provider_id, account_id,
                    status, duration_ms, streamed, request_body, response_body, error,
                    client_key_id, client_key_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    client_format,
                    model,
                    resolved_model,
                    provider_id,
                    account_id,
                    status,
                    duration_ms,
                    1 if streamed else 0,
                    _truncate(request_body),
                    _truncate(response_body),
                    error,
                    client_key_id,
                    client_key_label,
                ),
            )
            await db.execute(
                "DELETE FROM request_logs WHERE id NOT IN "
                "(SELECT id FROM request_logs ORDER BY id DESC LIMIT ?)",
                (rows,),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record request log: %s", e)


async def list_request_logs(
    db_path: str | Path,
    *,
    limit: int = 100,
    offset: int = 0,
    failed_attempts_only: bool = False,
) -> list[dict[str, Any]]:
    where = (
        f"WHERE r.request_id IS NOT NULL AND {_FAILED_ATTEMPT_PREDICATE}"
        if (failed_attempts_only)
        else ""
    )
    async with get_connection(db_path) as db:
        async with db.execute(
            f"""SELECT r.id, r.timestamp, r.request_id, r.client_format, r.model,
                      r.resolved_model, r.provider_id,
                      r.account_id, r.status, r.duration_ms, r.streamed, r.error,
                      r.client_key_id, r.client_key_label, k.name AS client_key_name
               FROM request_logs r
               LEFT JOIN api_keys k ON k.id = r.client_key_id
               {where}
               ORDER BY r.id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def attach_attempt_summaries(
    db_path: str | Path, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    request_ids = sorted({str(row["request_id"]) for row in rows if row.get("request_id")})
    signals: dict[str, tuple[int, int]] = {}
    outcome_attempts: dict[str, int] = {}
    if request_ids:
        placeholders = ",".join("?" for _ in request_ids)
        async with get_connection(db_path) as db:
            async with db.execute(
                f"""SELECT request_id, COUNT(*) AS total,
                           SUM(CASE WHEN outcome IN ('error', 'client_error')
                                    THEN 1 ELSE 0 END) AS failed
                    FROM attempt_signals WHERE request_id IN ({placeholders})
                    GROUP BY request_id""",
                request_ids,
            ) as cur:
                for item in await cur.fetchall():
                    signals[str(item["request_id"])] = (
                        int(item["total"]),
                        int(item["failed"] or 0),
                    )
            missing = [rid for rid in request_ids if rid not in signals]
            if missing:
                marks = ",".join("?" for _ in missing)
                async with db.execute(
                    f"""SELECT request_id, MAX(attempts) AS attempts FROM request_outcomes
                        WHERE request_id IN ({marks}) GROUP BY request_id""",
                    missing,
                ) as cur:
                    for item in await cur.fetchall():
                        outcome_attempts[str(item["request_id"])] = int(item["attempts"] or 0)
    summarized: list[dict[str, Any]] = []
    for row in rows:
        request_id = str(row["request_id"]) if row.get("request_id") else None
        attempt_count: int | None = None
        failed_count: int | None = None
        if request_id and request_id in signals:
            attempt_count, failed_count = signals[request_id]
        elif request_id and request_id in outcome_attempts:
            attempt_count = outcome_attempts[request_id]
        summarized.append(
            {
                **row,
                "attempt_count": attempt_count,
                "failed_attempt_count": failed_count,
                "attempt_details_available": failed_count is not None,
            }
        )
    return summarized


async def get_request_log(db_path: str | Path, log_id: int) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT r.*, k.name AS client_key_name
               FROM request_logs r
               LEFT JOIN api_keys k ON k.id = r.client_key_id
               WHERE r.id = ?""",
            (log_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def export_request_logs(db_path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async for row in iter_request_logs(db_path):
        rows.append(row)
    return rows


async def iter_request_logs(db_path: str | Path) -> AsyncIterator[dict[str, Any]]:
    """Stream request log rows one at a time without buffering the full table.

    Used by bounded-memory exporters (streaming HTTP responses and CLI file
    dumps) so a large request_logs table never has to be materialised in
    memory at once. Rows are yielded newest-first to match
    :func:`export_request_logs`.
    """
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM request_logs ORDER BY id DESC") as cur:
            while True:
                row = await cur.fetchone()
                if row is None:
                    break
                yield dict(row)


def encode_request_logs_ndjson(
    rows: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[bytes]:
    """Encode request log rows as newline-delimited JSON for streaming."""

    async def _generate() -> AsyncIterator[bytes]:
        async for row in rows:
            yield (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode()

    return _generate()


async def count_request_logs(db_path: str | Path, *, failed_attempts_only: bool = False) -> int:
    where = (
        f"WHERE r.request_id IS NOT NULL AND {_FAILED_ATTEMPT_PREDICATE}"
        if (failed_attempts_only)
        else ""
    )
    async with get_connection(db_path) as db:
        async with db.execute(f"SELECT COUNT(*) AS n FROM request_logs r {where}") as cur:
            row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def clear_request_logs(db_path: str | Path) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM request_logs")
        await db.commit()

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

ATTEMPT_SIGNAL_RETENTION_DAYS = 7
_PRUNE_MIN_INTERVAL_S = 3600.0
_last_prune: dict[str, float] = {}


def reset_attempt_signal_prune_throttle() -> None:
    _last_prune.clear()


async def record_attempt_signal(
    db_path: str | Path,
    *,
    model: str,
    account_id: str,
    provider_id: str | None,
    client_format: str | None,
    streamed: bool,
    outcome: str,
    status: int | None,
    ttft_ms: int | None,
    duration_ms: int,
    output_tokens: int,
    output_tps: float | None,
    failure_class: str | None = None,
    cooldown_decision: str | None = None,
    cooldown_seconds: int | None = None,
    request_id: str | None = None,
    attempt_order: int | None = None,
) -> None:
    try:
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO attempt_signals
                   (request_id, attempt_order, model, provider_id, account_id, client_format,
                    streamed, outcome, status, ttft_ms, duration_ms, output_tokens, output_tps,
                    failure_class, cooldown_decision, cooldown_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    attempt_order,
                    model,
                    provider_id,
                    account_id,
                    client_format,
                    1 if streamed else 0,
                    outcome,
                    status,
                    ttft_ms,
                    max(duration_ms, 0),
                    max(output_tokens, 0),
                    output_tps,
                    failure_class,
                    cooldown_decision,
                    cooldown_seconds,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to record attempt signal: %s", e)
        return
    await _maybe_prune(db_path)


async def _maybe_prune(db_path: str | Path) -> None:
    key = str(db_path)
    now = time.monotonic()
    last = _last_prune.get(key)
    if last is not None and now - last < _PRUNE_MIN_INTERVAL_S:
        return
    _last_prune[key] = now
    try:
        await prune_attempt_signals(db_path, ATTEMPT_SIGNAL_RETENTION_DAYS)
    except Exception as e:
        logger.warning("Attempt signal prune failed: %s", e)


async def prune_attempt_signals(
    db_path: str | Path, retention_days: int = ATTEMPT_SIGNAL_RETENTION_DAYS
) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "DELETE FROM attempt_signals WHERE timestamp < datetime('now', ?)",
            (f"-{int(retention_days)} days",),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def list_attempt_signals(db_path: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT id, timestamp, request_id, attempt_order, model, provider_id, account_id,
                      client_format, streamed, outcome, status, ttft_ms, duration_ms,
                      output_tokens, output_tps, failure_class, cooldown_decision,
                      cooldown_seconds
               FROM attempt_signals ORDER BY id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_attempt_health(db_path: str | Path, *, hours: int) -> dict[str, Any]:
    window_hours = max(1, min(int(hours), ATTEMPT_SIGNAL_RETENTION_DAYS * 24))
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT outcome, COALESCE(failure_class, '') AS failure_class, COUNT(*) AS n
               FROM attempt_signals
               WHERE timestamp >= datetime('now', ?)
               GROUP BY outcome, COALESCE(failure_class, '')""",
            (f"-{window_hours} hours",),
        ) as cur:
            rows = await cur.fetchall()
    ok = 0
    failed = 0
    aborted = 0
    by_class: dict[str, int] = {}
    for row in rows:
        count = int(row["n"])
        outcome = str(row["outcome"])
        if outcome == "ok":
            ok += count
        elif outcome == "aborted":
            aborted += count
        else:
            failed += count
            label = str(row["failure_class"]) or "unknown"
            by_class[label] = by_class.get(label, 0) + count
    judged = ok + failed
    return {
        "window_hours": window_hours,
        "total": judged,
        "ok": ok,
        "failed": failed,
        "aborted": aborted,
        "success_rate": round(ok * 100 / judged, 1) if judged else None,
        "failed_by_class": dict(sorted(by_class.items(), key=lambda item: -item[1])),
    }

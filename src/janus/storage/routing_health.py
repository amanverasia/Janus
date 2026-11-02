from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .database import get_connection

UNHEALTHY = "unhealthy"
HISTORY_STATUS = "routing_unhealthy"


@dataclass(frozen=True)
class RoutingHealth:
    reason: str
    status_code: int | None
    at: float


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M:%S")


def _epoch(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


async def mark_routing_unhealthy(
    db_path: str | Path,
    key_id: str,
    *,
    reason: str,
    status_code: int | None,
    at: float,
) -> bool:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT status, routing_health_status FROM upstream_keys WHERE id = ?", (key_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        was_unhealthy = row["routing_health_status"] == UNHEALTHY
        await db.execute(
            """UPDATE upstream_keys
               SET routing_health_status = ?, routing_health_reason = ?,
                   routing_health_status_code = ?, routing_health_at = ?
               WHERE id = ?""",
            (UNHEALTHY, reason, status_code, _iso(at), key_id),
        )
        if not was_unhealthy:
            await db.execute(
                """INSERT INTO upstream_key_history
                   (upstream_key_id, previous_status, new_status, notes)
                   VALUES (?, ?, ?, ?)""",
                (
                    key_id,
                    row["status"],
                    HISTORY_STATUS,
                    f"Repeated {reason.replace('_', ' ')} from routing"
                    + (f" (HTTP {status_code})" if status_code else ""),
                ),
            )
        await db.commit()
    return not was_unhealthy


async def clear_routing_unhealthy(db_path: str | Path, key_id: str) -> bool:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT status, routing_health_status FROM upstream_keys WHERE id = ?", (key_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["routing_health_status"] != UNHEALTHY:
            return False
        await db.execute(
            """UPDATE upstream_keys
               SET routing_health_status = NULL, routing_health_reason = NULL,
                   routing_health_status_code = NULL, routing_health_at = NULL
               WHERE id = ?""",
            (key_id,),
        )
        await db.execute(
            """INSERT INTO upstream_key_history
               (upstream_key_id, previous_status, new_status, notes)
               VALUES (?, ?, ?, ?)""",
            (key_id, HISTORY_STATUS, row["status"], "Routing health restored"),
        )
        await db.commit()
    return True


async def load_routing_health(db_path: str | Path) -> dict[str, RoutingHealth]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT id, routing_health_reason, routing_health_status_code, routing_health_at
               FROM upstream_keys
               WHERE routing_health_status = ? AND is_archived = 0 AND status != 'revoked'""",
            (UNHEALTHY,),
        ) as cur:
            rows = await cur.fetchall()
    return {
        str(row["id"]): RoutingHealth(
            reason=str(row["routing_health_reason"] or "auth_error"),
            status_code=(
                int(row["routing_health_status_code"])
                if row["routing_health_status_code"] is not None
                else None
            ),
            at=_epoch(row["routing_health_at"]),
        )
        for row in rows
    }

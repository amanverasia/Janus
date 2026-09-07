from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from janus.storage.database import get_connection


async def seed_usage(
    db_path: str | Path,
    rows: list[dict],
) -> None:
    """Seed usage rows, mirroring each into request_outcomes.

    Mirrors the real recording path: a successful request writes one usage
    row and one outcome row. Tests that need failure-only outcomes (which
    have no usage row) use ``seed_outcomes``.
    """
    for row in rows:
        ts = row.get("timestamp", datetime.datetime.now().isoformat())
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage
                   (timestamp, provider_id, model, account_id,
                    input_tokens, output_tokens, cache_creation_tokens,
                    cache_read_tokens, status, client_key_id, client_key_label, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    row.get("provider_id"),
                    row.get("model"),
                    row.get("account_id"),
                    row.get("input_tokens", 0),
                    row.get("output_tokens", 0),
                    row.get("cache_creation_tokens", 0),
                    row.get("cache_read_tokens", 0),
                    row.get("status", 200),
                    row.get("client_key_id"),
                    row.get("client_key_label"),
                    row.get("cost", 0.0),
                ),
            )
            await db.execute(
                """INSERT INTO request_outcomes
                   (timestamp, model, provider_id, account_id, status,
                    client_key_id, client_key_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    row.get("model"),
                    row.get("provider_id"),
                    row.get("account_id"),
                    row.get("status", 200),
                    row.get("client_key_id"),
                    row.get("client_key_label"),
                ),
            )
            await db.commit()


async def seed_outcomes(
    db_path: str | Path,
    rows: list[dict[str, Any]],
) -> None:
    """Seed request_outcomes rows directly (terminal failures, retries, etc.)."""
    for row in rows:
        ts = row.get("timestamp", datetime.datetime.now().isoformat())
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO request_outcomes
                   (timestamp, client_format, model, provider_id, account_id, status,
                    duration_ms, streamed, attempts, client_key_id, client_key_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    row.get("client_format"),
                    row.get("model"),
                    row.get("provider_id"),
                    row.get("account_id"),
                    row.get("status", 200),
                    row.get("duration_ms"),
                    1 if row.get("streamed") else 0,
                    row.get("attempts", 1),
                    row.get("client_key_id"),
                    row.get("client_key_label"),
                ),
            )
            await db.commit()

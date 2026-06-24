from __future__ import annotations

import datetime
from pathlib import Path

from janus.storage.database import get_connection


async def seed_usage(
    db_path: str | Path,
    rows: list[dict],
) -> None:
    for row in rows:
        ts = row.get("timestamp", datetime.datetime.now().isoformat())
        async with get_connection(db_path) as db:
            await db.execute(
                """INSERT INTO usage
                   (timestamp, provider_id, model, account_id,
                    input_tokens, output_tokens, cache_creation_tokens,
                    cache_read_tokens, status, client_key_id, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    row.get("cost", 0.0),
                ),
            )
            await db.commit()

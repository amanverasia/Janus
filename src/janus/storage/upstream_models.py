from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .database import get_connection


async def replace_models_for_key(
    db_path: str | Path,
    *,
    upstream_key_id: str,
    provider_id: str,
    models: list[dict[str, Any]],
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM upstream_models WHERE upstream_key_id = ?",
            (upstream_key_id,),
        )
        for model in models:
            await db.execute(
                """INSERT INTO upstream_models
                   (id, provider_id, upstream_key_id, model_id, display_name,
                    context_window, max_output_tokens, pricing_input, pricing_output,
                    pricing_cached_input, capabilities, benchmarks, tokens_per_second)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    provider_id,
                    upstream_key_id,
                    model["model_id"],
                    model.get("display_name"),
                    model.get("context_window"),
                    model.get("max_output_tokens"),
                    model.get("pricing_input"),
                    model.get("pricing_output"),
                    model.get("pricing_cached_input"),
                    model.get("capabilities")
                    if isinstance(model.get("capabilities"), str)
                    else json.dumps(model.get("capabilities"))
                    if model.get("capabilities") is not None
                    else None,
                    model.get("benchmarks")
                    if isinstance(model.get("benchmarks"), str)
                    else json.dumps(model.get("benchmarks"))
                    if model.get("benchmarks") is not None
                    else None,
                    model.get("tokens_per_second"),
                ),
            )
        await db.commit()


async def list_models_for_key(db_path: str | Path, upstream_key_id: str) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM upstream_models WHERE upstream_key_id = ? ORDER BY model_id",
            (upstream_key_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]

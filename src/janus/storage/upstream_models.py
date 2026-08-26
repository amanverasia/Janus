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


async def list_model_ids_for_keys(
    db_path: str | Path,
    upstream_key_ids: list[str],
) -> dict[str, list[str]]:
    if not upstream_key_ids:
        return {}
    placeholders = ", ".join("?" for _ in upstream_key_ids)
    key_query = f"""SELECT id FROM upstream_keys
                    WHERE id IN ({placeholders}) AND models_discovered_at IS NOT NULL"""
    model_query = f"""SELECT upstream_key_id, model_id
                      FROM upstream_models
                      WHERE upstream_key_id IN ({placeholders}) AND is_available = 1
                      ORDER BY upstream_key_id, model_id"""
    async with get_connection(db_path) as db:
        async with db.execute(key_query, upstream_key_ids) as cur:
            discovered_keys = await cur.fetchall()
        async with db.execute(model_query, upstream_key_ids) as cur:
            rows = await cur.fetchall()
    result: dict[str, list[str]] = {str(row["id"]): [] for row in discovered_keys}
    for row in rows:
        key_id = str(row["upstream_key_id"])
        model_id = str(row["model_id"])
        models = result.setdefault(key_id, [])
        if model_id not in models:
            models.append(model_id)
    return result


async def list_live_model_ids_for_provider(
    db_path: str | Path,
    inventory_provider_id: str,
) -> list[str]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT DISTINCT model_id
               FROM upstream_models
               WHERE provider_id = ? AND is_available = 1
               ORDER BY model_id""",
            (inventory_provider_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [str(row["model_id"]) for row in rows]

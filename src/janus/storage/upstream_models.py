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
    model_query = f"""SELECT DISTINCT upstream_key_id, model_id
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
        result.setdefault(str(row["upstream_key_id"]), []).append(str(row["model_id"]))
    return result


_DISTINCT_DISCOVERED_PACK_SEP = "\x1f"
_DISTINCT_DISCOVERED_MODELS_QUERY = f"""
SELECT
    provider_id,
    model_id,
    MIN(created_at) AS first_created_at,
    MIN(id) AS first_id,
    MAX(CASE WHEN display_name IS NOT NULL
        THEN created_at || '{_DISTINCT_DISCOVERED_PACK_SEP}' || id
             || '{_DISTINCT_DISCOVERED_PACK_SEP}' || display_name END) AS display_name_packed,
    MAX(CASE WHEN context_window IS NOT NULL
        THEN created_at || '{_DISTINCT_DISCOVERED_PACK_SEP}' || id
             || '{_DISTINCT_DISCOVERED_PACK_SEP}' || context_window END) AS context_window_packed,
    MAX(CASE WHEN max_output_tokens IS NOT NULL
        THEN created_at || '{_DISTINCT_DISCOVERED_PACK_SEP}' || id
             || '{_DISTINCT_DISCOVERED_PACK_SEP}' || max_output_tokens END) AS max_output_packed,
    MAX(CASE WHEN capabilities IS NOT NULL
        THEN created_at || '{_DISTINCT_DISCOVERED_PACK_SEP}' || id
             || '{_DISTINCT_DISCOVERED_PACK_SEP}' || capabilities END) AS capabilities_packed
FROM upstream_models
WHERE is_available = 1
GROUP BY provider_id, model_id
ORDER BY first_created_at, first_id
"""


def _unpack_discovered_field(packed: Any, *, as_int: bool = False) -> Any:
    if packed is None:
        return None
    value = str(packed).rsplit(_DISTINCT_DISCOVERED_PACK_SEP, 2)[-1]
    if as_int:
        try:
            return int(value)
        except ValueError:
            return None
    return value


async def list_distinct_discovered_models(db_path: str | Path) -> list[dict[str, Any]]:
    """Available discoveries collapsed to one row per (provider_id, model_id) pair.

    Cardinality scales with distinct provider/model pairs, not with the
    per-key row count in ``upstream_models``. Metadata precedence: each field
    (display_name, context_window, max_output_tokens, capabilities) takes its
    value from the most recent observation (highest ``created_at``, then
    highest ``id``) where that field is non-NULL; older non-NULL values only
    survive when every newer observation of the pair has NULL for the field.
    Rows come back ordered by the pair's earliest observation, matching the
    legacy full-scan order.
    """
    async with get_connection(db_path) as db:
        async with db.execute(_DISTINCT_DISCOVERED_MODELS_QUERY) as cur:
            rows = await cur.fetchall()
    return [
        {
            "provider_id": str(row["provider_id"]),
            "model_id": str(row["model_id"]),
            "display_name": _unpack_discovered_field(row["display_name_packed"]),
            "context_window": _unpack_discovered_field(row["context_window_packed"], as_int=True),
            "max_output_tokens": _unpack_discovered_field(row["max_output_packed"], as_int=True),
            "capabilities": _unpack_discovered_field(row["capabilities_packed"]),
        }
        for row in rows
    ]


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

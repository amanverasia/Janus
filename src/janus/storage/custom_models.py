from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .database import get_connection


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _decode_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["input_modalities"] = _json_list(item.get("input_modalities"))
    item["reasoning_efforts"] = _json_list(item.get("reasoning_efforts"))
    item["capabilities"] = _json_dict(item.get("capabilities"))
    item["is_enabled"] = bool(item.get("is_enabled"))
    return item


async def list_custom_models(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if provider_id is not None:
        clauses.append("provider_id = ?")
        params.append(provider_id)
    if enabled_only:
        clauses.append("is_enabled = 1")
    query = "SELECT * FROM custom_models"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY provider_id, model_id"
    async with get_connection(db_path) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [_decode_row(row) for row in rows]


async def get_custom_model(
    db_path: str | Path,
    custom_model_id: str,
) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM custom_models WHERE id = ?",
            (custom_model_id,),
        ) as cur:
            row = await cur.fetchone()
    return _decode_row(row) if row is not None else None


async def create_custom_model(
    db_path: str | Path,
    data: dict[str, Any],
) -> dict[str, Any]:
    custom_model_id = str(data.get("id") or uuid.uuid4())
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO custom_models
               (id, provider_id, model_id, display_name, context_window,
                max_output_tokens, input_modalities, reasoning_efforts,
                capabilities, is_enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                custom_model_id,
                data["provider_id"],
                data["model_id"],
                data.get("display_name"),
                data.get("context_window"),
                data.get("max_output_tokens"),
                json.dumps(_json_list(data.get("input_modalities"))),
                json.dumps(_json_list(data.get("reasoning_efforts"))),
                json.dumps(_json_dict(data.get("capabilities"))),
                1 if data.get("is_enabled", True) else 0,
            ),
        )
        await db.commit()
    result = await get_custom_model(db_path, custom_model_id)
    if result is None:
        raise RuntimeError("Custom model was not created")
    return result


async def update_custom_model(
    db_path: str | Path,
    custom_model_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    current = await get_custom_model(db_path, custom_model_id)
    if current is None:
        return None

    def field(name: str) -> Any:
        return data[name] if name in data else current.get(name)

    async with get_connection(db_path) as db:
        await db.execute(
            """UPDATE custom_models SET
                 provider_id = ?, model_id = ?, display_name = ?, context_window = ?,
                 max_output_tokens = ?, input_modalities = ?, reasoning_efforts = ?,
                 capabilities = ?, is_enabled = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (
                field("provider_id"),
                field("model_id"),
                field("display_name"),
                field("context_window"),
                field("max_output_tokens"),
                json.dumps(_json_list(field("input_modalities"))),
                json.dumps(_json_list(field("reasoning_efforts"))),
                json.dumps(_json_dict(field("capabilities"))),
                1 if field("is_enabled") else 0,
                custom_model_id,
            ),
        )
        await db.commit()
    return await get_custom_model(db_path, custom_model_id)


async def toggle_custom_model(
    db_path: str | Path,
    custom_model_id: str,
) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """UPDATE custom_models
               SET is_enabled = 1 - is_enabled, updated_at = datetime('now')
               WHERE id = ?""",
            (custom_model_id,),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_custom_model(db_path, custom_model_id)


async def delete_custom_model(
    db_path: str | Path,
    custom_model_id: str,
) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("DELETE FROM custom_models WHERE id = ?", (custom_model_id,))
        await db.commit()
    return cursor.rowcount > 0

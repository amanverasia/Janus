from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from janus.inventory.url_guard import mask_key

from .database import get_connection


def _new_key_id() -> str:
    return str(uuid.uuid4())


async def create_upstream_key(
    db_path: str | Path,
    *,
    provider_id: str,
    key_value: str,
    key_label: str | None = None,
    custom_base_url: str | None = None,
    source_node: str | None = None,
    priority: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key_id = _new_key_id()
    record = {
        "id": key_id,
        "provider_id": provider_id,
        "key_label": key_label,
        "key_value": key_value,
        "key_masked": mask_key(key_value),
        "custom_base_url": custom_base_url,
        "status": "pending_validation",
        "is_valid": 0,
        "health_status": "healthy",
        "is_usable": 0,
        "usability_status": "unknown",
        "priority": priority,
        "metadata": json.dumps(metadata) if metadata else None,
        "source_node": source_node,
    }
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO upstream_keys
               (id, provider_id, key_label, key_value, key_masked, custom_base_url,
                status, is_valid, health_status, is_usable, usability_status,
                priority, metadata, source_node)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["id"],
                record["provider_id"],
                record["key_label"],
                record["key_value"],
                record["key_masked"],
                record["custom_base_url"],
                record["status"],
                record["is_valid"],
                record["health_status"],
                record["is_usable"],
                record["usability_status"],
                record["priority"],
                record["metadata"],
                record["source_node"],
            ),
        )
        await db.commit()
    return record


async def get_upstream_key(db_path: str | Path, key_id: str) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute("SELECT * FROM upstream_keys WHERE id = ?", (key_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_upstream_keys(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM upstream_keys"
    clauses: list[str] = ["status != 'revoked'"]
    params: list[Any] = []
    if provider_id is not None:
        clauses.append("provider_id = ?")
        params.append(provider_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append("(key_label LIKE ? OR key_masked LIKE ? OR provider_id LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY priority DESC, created_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    async with get_connection(db_path) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def list_upstream_keys_masked(db_path: str | Path, **kwargs: Any) -> list[dict[str, Any]]:
    keys = await list_upstream_keys(db_path, **kwargs)
    masked: list[dict[str, Any]] = []
    for key in keys:
        item = dict(key)
        item.pop("key_value", None)
        masked.append(item)
    return masked


async def update_upstream_key(
    db_path: str | Path,
    key_id: str,
    data: dict[str, Any],
) -> None:
    allowed = {
        "provider_id",
        "key_label",
        "key_value",
        "key_masked",
        "custom_base_url",
        "status",
        "is_valid",
        "health_status",
        "health_warnings",
        "is_usable",
        "usability_status",
        "usability_note",
        "credits_remaining",
        "credits_total",
        "credits_used",
        "rate_limit_rpm",
        "rate_limit_tpm",
        "rate_limit_rpd",
        "usage_current_rpm",
        "usage_current_tpm",
        "daily_credit_limit",
        "daily_credit_used",
        "daily_credit_date",
        "is_daily_limited",
        "priority",
        "metadata",
        "source_node",
        "last_checked_at",
        "last_error",
    }
    updates: list[str] = []
    params: list[Any] = []
    for field, value in data.items():
        if field not in allowed:
            continue
        if field == "metadata" and isinstance(value, dict):
            value = json.dumps(value)
        if field == "health_warnings" and isinstance(value, list):
            value = json.dumps(value)
        updates.append(f"{field} = ?")
        params.append(value)
    if not updates:
        return
    updates.append("updated_at = datetime('now')")
    params.append(key_id)
    async with get_connection(db_path) as db:
        await db.execute(
            f"UPDATE upstream_keys SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()


async def delete_upstream_key(db_path: str | Path, key_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM upstream_models WHERE upstream_key_id = ?", (key_id,))
        await db.execute("DELETE FROM upstream_key_history WHERE upstream_key_id = ?", (key_id,))
        await db.execute("DELETE FROM upstream_keys WHERE id = ?", (key_id,))
        await db.commit()


async def record_upstream_key_history(
    db_path: str | Path,
    *,
    upstream_key_id: str,
    new_status: str,
    previous_status: str | None = None,
    credits_remaining: float | None = None,
    notes: str | None = None,
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO upstream_key_history
               (upstream_key_id, previous_status, new_status, credits_remaining, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (upstream_key_id, previous_status, new_status, credits_remaining, notes),
        )
        await db.commit()


async def count_upstream_keys(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM upstream_keys WHERE status != 'revoked'"
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def count_pending_upstream_keys(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM upstream_keys WHERE status = 'pending_validation'"
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def get_upstream_keys_by_ids(
    db_path: str | Path, key_ids: list[str]
) -> list[dict[str, Any]]:
    if not key_ids:
        return []
    placeholders = ", ".join("?" for _ in key_ids)
    query = f"SELECT * FROM upstream_keys WHERE id IN ({placeholders}) ORDER BY created_at"
    async with get_connection(db_path) as db:
        async with db.execute(query, key_ids) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def export_upstream_keys(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT k.*, p.display_name as provider_display_name
               FROM upstream_keys k
               JOIN inventory_providers p ON k.provider_id = p.id
               WHERE k.status != 'revoked'
               ORDER BY k.provider_id, k.created_at"""
        ) as cur:
            rows = await cur.fetchall()
    exported: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        metadata = item.get("metadata")
        if isinstance(metadata, str):
            try:
                item["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                item["metadata"] = None
        exported.append(item)
    return exported

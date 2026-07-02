from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from janus.inventory.key_encryption import (
    decrypt_key_value,
    encrypt_key_value,
    encryption_enabled,
    hash_upstream_key,
    is_encrypted_value,
)
from janus.inventory.url_guard import mask_key

from .database import get_connection

SORT_COLUMNS: dict[str, str] = {
    "credits": "k.credits_remaining",
    "provider": "p.display_name",
    "status": "k.status",
    "rate_limit": "k.rate_limit_rpm",
    "last_checked": "k.last_checked_at",
}

DEFAULT_PAGE_SIZE = 25


def _new_key_id() -> str:
    return str(uuid.uuid4())


def _prepare_key_storage(key_value: str) -> tuple[str, str, str]:
    stored_value = encrypt_key_value(key_value)
    return stored_value, hash_upstream_key(key_value), mask_key(key_value)


def _decode_upstream_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    key_value = item.get("key_value")
    if isinstance(key_value, str):
        item["key_value"] = decrypt_key_value(key_value)
    return item


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
    stored_value, key_hash, key_masked = _prepare_key_storage(key_value)
    record = {
        "id": key_id,
        "provider_id": provider_id,
        "key_label": key_label,
        "key_value": key_value,
        "key_hash": key_hash,
        "key_masked": key_masked,
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
               (id, provider_id, key_label, key_value, key_hash, key_masked, custom_base_url,
                status, is_valid, health_status, is_usable, usability_status,
                priority, metadata, source_node)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["id"],
                record["provider_id"],
                record["key_label"],
                stored_value,
                record["key_hash"],
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
    return _decode_upstream_row(row) if row else None


async def find_upstream_key_by_value(db_path: str | Path, key_value: str) -> dict[str, Any] | None:
    key_hash = hash_upstream_key(key_value)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM upstream_keys WHERE key_hash = ? AND status != 'revoked' LIMIT 1",
            (key_hash,),
        ) as cur:
            row = await cur.fetchone()
    return _decode_upstream_row(row) if row else None


async def find_upstream_key_by_value_and_provider(
    db_path: str | Path,
    key_value: str,
    provider_id: str,
) -> dict[str, Any] | None:
    key_hash = hash_upstream_key(key_value)
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT * FROM upstream_keys
               WHERE key_hash = ? AND provider_id = ? AND status != 'revoked'
               LIMIT 1""",
            (key_hash, provider_id),
        ) as cur:
            row = await cur.fetchone()
    return _decode_upstream_row(row) if row else None


def _list_filters(
    *,
    provider_id: str | None,
    status: str | None,
    search: str | None,
) -> tuple[str, list[Any]]:
    clauses = ["k.status != 'revoked'"]
    params: list[Any] = []
    if provider_id:
        clauses.append("k.provider_id = ?")
        params.append(provider_id)
    if status:
        clauses.append("k.status = ?")
        params.append(status)
    if search:
        clauses.append("(k.key_label LIKE ? OR k.key_masked LIKE ? OR k.provider_id LIKE ?)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
    return " AND ".join(clauses), params


def _normalize_sort(sort: str) -> str:
    return sort if sort in SORT_COLUMNS else "credits"


def _normalize_direction(direction: str) -> str:
    return "ASC" if direction.lower() == "asc" else "DESC"


async def count_upstream_keys_filtered(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> int:
    where, params = _list_filters(provider_id=provider_id, status=status, search=search)
    query = f"""
        SELECT COUNT(*)
        FROM upstream_keys k
        JOIN inventory_providers p ON k.provider_id = p.id
        WHERE {where}
    """
    async with get_connection(db_path) as db:
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def list_upstream_keys_page(
    db_path: str | Path,
    *,
    provider_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    sort: str = "credits",
    direction: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    masked: bool = True,
) -> list[dict[str, Any]]:
    where, params = _list_filters(provider_id=provider_id, status=status, search=search)
    sort_key = _normalize_sort(sort)
    sort_col = SORT_COLUMNS[sort_key]
    sort_dir = _normalize_direction(direction)
    null_dir = "ASC" if sort_dir == "DESC" else "DESC"
    query = f"""
        SELECT k.*,
               p.display_name AS provider_display_name,
               p.name AS provider_name,
               p.billing_model AS provider_billing_model
        FROM upstream_keys k
        JOIN inventory_providers p ON k.provider_id = p.id
        WHERE {where}
        ORDER BY ({sort_col} IS NULL) {null_dir}, {sort_col} {sort_dir}, k.created_at DESC
        LIMIT ? OFFSET ?
    """
    page_params = [*params, limit, offset]
    async with get_connection(db_path) as db:
        async with db.execute(query, page_params) as cur:
            rows = await cur.fetchall()
    items = [_decode_upstream_row(row) for row in rows]
    if masked:
        for item in items:
            item.pop("key_value", None)
    return items


async def list_upstream_key_history(
    db_path: str | Path,
    upstream_key_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT id, upstream_key_id, previous_status, new_status,
                      credits_remaining, notes, changed_at
               FROM upstream_key_history
               WHERE upstream_key_id = ?
               ORDER BY changed_at DESC
               LIMIT ?""",
            (upstream_key_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_upstream_key_detail(db_path: str | Path, key_id: str) -> dict[str, Any] | None:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT k.*,
                      p.display_name AS provider_display_name,
                      p.name AS provider_name,
                      p.billing_model AS provider_billing_model
               FROM upstream_keys k
               JOIN inventory_providers p ON k.provider_id = p.id
               WHERE k.id = ? AND k.status != 'revoked'""",
            (key_id,),
        ) as cur:
            row = await cur.fetchone()
    return _decode_upstream_row(row) if row else None


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
    return [_decode_upstream_row(row) for row in rows]


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
        "key_hash",
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
    payload = dict(data)
    if "key_value" in payload and isinstance(payload["key_value"], str):
        stored_value, key_hash, key_masked = _prepare_key_storage(payload["key_value"])
        payload["key_value"] = stored_value
        payload["key_hash"] = key_hash
        payload["key_masked"] = key_masked
    for field, value in payload.items():
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


async def list_routable_upstream_keys(
    db_path: str | Path,
    inventory_provider_id: str,
) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT * FROM upstream_keys
               WHERE provider_id = ?
                 AND status = 'active'
                 AND is_valid = 1
                 AND is_usable = 1
                 AND (
                   is_daily_limited = 0
                   OR daily_credit_limit IS NULL
                   OR daily_credit_used IS NULL
                   OR daily_credit_used < daily_credit_limit
                 )
               ORDER BY priority DESC,
                        CASE WHEN credits_remaining IS NULL THEN 1 ELSE 0 END,
                        credits_remaining DESC,
                        created_at ASC""",
            (inventory_provider_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_decode_upstream_row(row) for row in rows]


async def count_pending_upstream_keys(db_path: str | Path) -> int:
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM upstream_keys WHERE status = 'pending_validation'"
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return 0
    return int(row[0])


async def get_upstream_keys_by_ids(db_path: str | Path, key_ids: list[str]) -> list[dict[str, Any]]:
    if not key_ids:
        return []
    placeholders = ", ".join("?" for _ in key_ids)
    query = f"SELECT * FROM upstream_keys WHERE id IN ({placeholders}) ORDER BY created_at"
    async with get_connection(db_path) as db:
        async with db.execute(query, key_ids) as cur:
            rows = await cur.fetchall()
    return [_decode_upstream_row(row) for row in rows]


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
        item = _decode_upstream_row(row)
        metadata = item.get("metadata")
        if isinstance(metadata, str):
            try:
                item["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                item["metadata"] = None
        exported.append(item)
    return exported


async def reencrypt_plaintext_upstream_keys(db_path: str | Path) -> int:
    if not encryption_enabled():
        raise RuntimeError("INVENTORY_ENCRYPTION_KEY must be set to encrypt upstream keys")
    converted = 0
    async with get_connection(db_path) as db:
        async with db.execute("SELECT id, key_value FROM upstream_keys") as cur:
            rows = await cur.fetchall()
        for row in rows:
            stored = row["key_value"]
            if not isinstance(stored, str) or is_encrypted_value(stored):
                continue
            plaintext = stored
            encrypted, key_hash, key_masked = _prepare_key_storage(plaintext)
            await db.execute(
                """UPDATE upstream_keys
                   SET key_value = ?, key_hash = ?, key_masked = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (encrypted, key_hash, key_masked, row["id"]),
            )
            converted += 1
        await db.commit()
    return converted


async def count_storage_encryption_state(db_path: str | Path) -> dict[str, int]:
    encrypted = 0
    plaintext = 0
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT key_value FROM upstream_keys WHERE status != 'revoked'"
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        stored = row["key_value"]
        if isinstance(stored, str) and is_encrypted_value(stored):
            encrypted += 1
        else:
            plaintext += 1
    return {"encrypted": encrypted, "plaintext": plaintext, "total": encrypted + plaintext}

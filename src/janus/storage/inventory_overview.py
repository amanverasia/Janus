from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import get_connection


async def get_inventory_summary(db_path: str | Path) -> dict[str, int]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                 SUM(CASE WHEN status = 'invalid' THEN 1 ELSE 0 END) as invalid,
                 SUM(CASE WHEN is_usable = 1 AND status != 'revoked' THEN 1 ELSE 0 END) as usable,
                 SUM(CASE WHEN status = 'pending_validation' THEN 1 ELSE 0 END) as pending
               FROM upstream_keys
               WHERE status != 'revoked'"""
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM inventory_providers WHERE is_active = 1"
        ) as cur:
            providers_row = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(DISTINCT model_id) FROM upstream_models WHERE is_available = 1"
        ) as cur:
            models_row = await cur.fetchone()
    if row is None:
        counts = {"total": 0, "active": 0, "invalid": 0, "usable": 0, "pending": 0}
    else:
        counts = {key: int(row[key] or 0) for key in row.keys()}
    return {
        **counts,
        "providers": int(providers_row[0]) if providers_row else 0,
        "models": int(models_row[0]) if models_row else 0,
    }


async def get_provider_cards(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                 p.id,
                 p.name,
                 p.display_name,
                 p.is_direct,
                 p.routing_note,
                 p.billing_model,
                 COUNT(k.id) as total_keys,
                 SUM(CASE WHEN k.status = 'active' THEN 1 ELSE 0 END) as active_keys,
                 SUM(CASE WHEN k.is_usable = 1 THEN 1 ELSE 0 END) as usable_keys,
                 SUM(CASE WHEN k.status = 'invalid' THEN 1 ELSE 0 END) as invalid_keys,
                 ROUND(COALESCE(SUM(k.credits_remaining), 0), 2) as total_credits
               FROM inventory_providers p
               LEFT JOIN upstream_keys k
                 ON p.id = k.provider_id AND k.status != 'revoked'
               WHERE p.is_active = 1
               GROUP BY p.id
               ORDER BY usable_keys DESC, active_keys DESC, total_keys DESC"""
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_recent_activity(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                 h.id,
                 h.upstream_key_id,
                 h.previous_status,
                 h.new_status,
                 h.credits_remaining,
                 h.notes,
                 h.changed_at,
                 k.key_masked,
                 k.key_label,
                 p.display_name as provider_display_name
               FROM upstream_key_history h
               JOIN upstream_keys k ON h.upstream_key_id = k.id
               JOIN inventory_providers p ON k.provider_id = p.id
               ORDER BY h.changed_at DESC
               LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_credit_summary(db_path: str | Path) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT
                 p.display_name,
                 p.name,
                 p.billing_model,
                 COUNT(k.id) as key_count,
                 ROUND(COALESCE(SUM(k.credits_remaining), 0), 2) as total_remaining,
                 ROUND(COALESCE(SUM(k.credits_total), 0), 2) as total_cap,
                 ROUND(COALESCE(SUM(k.credits_used), 0), 2) as total_used
               FROM inventory_providers p
               LEFT JOIN upstream_keys k
                 ON p.id = k.provider_id AND k.status = 'active' AND k.is_valid = 1
               WHERE p.is_active = 1
               GROUP BY p.id
               HAVING key_count > 0
               ORDER BY total_remaining DESC"""
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_best_upstream_keys(db_path: str | Path) -> list[dict[str, Any]]:
    from janus.inventory.key_encryption import decrypt_key_value

    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT k.id, k.key_value, k.key_label, k.key_masked, k.provider_id,
                      p.display_name AS provider_display_name,
                      p.name AS provider_name,
                      k.credits_remaining, k.credits_total, k.rate_limit_rpm,
                      k.is_usable, k.usability_status
               FROM upstream_keys k
               JOIN inventory_providers p ON k.provider_id = p.id
               WHERE k.status = 'active'
                 AND k.is_usable = 1
                 AND k.credits_remaining IS NOT NULL
                 AND k.credits_remaining > 0
               ORDER BY k.credits_remaining DESC"""
        ) as cur:
            rows = await cur.fetchall()
    best_by_provider: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        key_value = item.get("key_value")
        if isinstance(key_value, str):
            item["key_value"] = decrypt_key_value(key_value)
        provider_id = str(item["provider_id"])
        if provider_id not in best_by_provider:
            best_by_provider[provider_id] = item
    return list(best_by_provider.values())


async def get_top_keys_per_provider(
    db_path: str | Path,
    *,
    per_provider: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """WITH ranked AS (
                 SELECT k.id, k.key_masked, k.key_label, k.provider_id, k.credits_remaining,
                        k.status, k.is_usable, p.display_name AS provider_display_name,
                        ROW_NUMBER() OVER (
                          PARTITION BY k.provider_id
                          ORDER BY k.credits_remaining DESC NULLS LAST, k.updated_at DESC
                        ) AS rn
                 FROM upstream_keys k
                 JOIN inventory_providers p ON k.provider_id = p.id
                 WHERE k.status = 'active' AND k.is_valid = 1 AND k.is_usable = 1
               )
               SELECT id, key_masked, key_label, provider_id, credits_remaining,
                      status, is_usable, provider_display_name
               FROM ranked
               WHERE rn <= ?
               ORDER BY provider_display_name, rn""",
            (per_provider,),
        ) as cur:
            rows = await cur.fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        provider_id = str(item["provider_id"])
        grouped.setdefault(provider_id, []).append(item)
    return grouped

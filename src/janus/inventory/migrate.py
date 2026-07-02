from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from janus.inventory.url_guard import detect_provider_from_key
from janus.storage.database import init_db
from janus.storage.upstream_keys import create_upstream_key, update_upstream_key


def _parse_export_payload(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        keys = raw.get("keys")
        if isinstance(keys, list):
            return [item for item in keys if isinstance(item, dict)]
    raise ValueError("Expected export JSON with a top-level 'keys' array or a bare array")


async def import_dashboard_export(db_path: Path, export_path: Path, *, dry_run: bool) -> int:
    payload = json.loads(export_path.read_text())
    rows = _parse_export_payload(payload)
    if not dry_run:
        await init_db(db_path)

    imported = 0
    for row in rows:
        key_value = row.get("key_value") or row.get("key")
        if not key_value:
            continue
        provider_id = (
            row.get("provider_id") or detect_provider_from_key(str(key_value)) or "unidentified"
        )
        if dry_run:
            imported += 1
            continue

        record = await create_upstream_key(
            db_path,
            provider_id=str(provider_id),
            key_value=str(key_value),
            key_label=row.get("key_label"),
            custom_base_url=row.get("custom_base_url"),
            source_node=row.get("node_id") or row.get("source_node"),
            priority=int(row.get("priority") or 0),
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else None,
        )
        await update_upstream_key(
            db_path,
            record["id"],
            {
                "status": row.get("status", "pending_validation"),
                "is_valid": int(bool(row.get("is_valid"))),
                "credits_remaining": row.get("credits_remaining"),
                "credits_total": row.get("credits_total"),
                "credits_used": row.get("credits_used"),
                "health_status": row.get("health_status"),
                "is_usable": int(bool(row.get("is_usable"))),
                "usability_status": row.get("usability_status"),
                "usability_note": row.get("usability_note"),
                "last_checked_at": row.get("last_checked_at"),
                "last_error": row.get("last_error"),
            },
        )
        imported += 1
    return imported

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from janus.inventory.url_guard import detect_provider_from_key
from janus.storage.database import init_db
from janus.storage.providers_db import count_provider_encryption_state
from janus.storage.upstream_keys import (
    count_storage_encryption_state,
    count_upstream_keys,
    create_upstream_key,
    list_upstream_keys,
    update_upstream_key,
)


def _parse_export_payload(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        keys = raw.get("keys")
        if isinstance(keys, list):
            return [item for item in keys if isinstance(item, dict)]
    raise ValueError("Expected export JSON with a top-level 'keys' array or a bare array")


def load_export_payload(export_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(export_path.read_text())
    return _parse_export_payload(payload)


def fetch_export_payload(url: str) -> list[dict[str, Any]]:
    with urlopen(url) as response:
        payload = json.loads(response.read())
    return _parse_export_payload(payload)


async def verify_inventory(db_path: Path) -> dict[str, Any]:
    keys = await list_upstream_keys(db_path)
    by_status: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    routable = 0
    for key in keys:
        by_status[key["status"]] = by_status.get(key["status"], 0) + 1
        by_provider[key["provider_id"]] = by_provider.get(key["provider_id"], 0) + 1
        if key["status"] == "active" and key["is_valid"] and key["is_usable"]:
            routable += 1
    encryption = await count_storage_encryption_state(db_path)
    provider_encryption = await count_provider_encryption_state(db_path)
    return {
        "total": await count_upstream_keys(db_path),
        "routable": routable,
        "by_status": dict(sorted(by_status.items())),
        "by_provider": dict(sorted(by_provider.items(), key=lambda item: (-item[1], item[0]))),
        "encryption": encryption,
        "provider_encryption": provider_encryption,
    }


def format_inventory_verification(summary: dict[str, Any]) -> str:
    lines = [
        f"Total upstream keys: {summary['total']}",
        f"Routable keys: {summary['routable']}",
        "By status:",
    ]
    for status, count in summary["by_status"].items():
        lines.append(f"  {status}: {count}")
    lines.append("Top providers:")
    for provider_id, count in list(summary["by_provider"].items())[:10]:
        lines.append(f"  {provider_id}: {count}")
    encryption = summary["encryption"]
    lines.append(
        "Upstream key encryption: "
        f"{encryption['encrypted']} encrypted, {encryption['plaintext']} plaintext "
        f"({encryption['total']} total)"
    )
    provider_encryption = summary["provider_encryption"]
    lines.append(
        "Provider credential encryption: "
        f"{provider_encryption['encrypted']} encrypted, "
        f"{provider_encryption['plaintext']} plaintext "
        f"({provider_encryption['total']} total)"
    )
    return "\n".join(lines)


async def import_dashboard_rows(
    db_path: Path,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> int:
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


async def import_dashboard_export(db_path: Path, export_path: Path, *, dry_run: bool) -> int:
    rows = load_export_payload(export_path)
    return await import_dashboard_rows(db_path, rows, dry_run=dry_run)


async def import_dashboard_json(db_path: Path, data: bytes, *, dry_run: bool) -> int:
    payload = json.loads(data)
    rows = _parse_export_payload(payload)
    return await import_dashboard_rows(db_path, rows, dry_run=dry_run)

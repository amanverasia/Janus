from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from janus.inventory.url_guard import detect_provider_from_key
from janus.storage.database import init_db
from janus.storage.providers_db import count_provider_encryption_state
from janus.storage.upstream_keys import (
    count_storage_encryption_state,
    count_upstream_keys,
    import_upstream_keys_atomic,
    list_upstream_keys,
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
    outcome = await import_dashboard_rows_with_ids(db_path, rows, dry_run=dry_run)
    return outcome.imported


class ImportRowError(ValueError):
    def __init__(self, row_number: int) -> None:
        super().__init__(f"Row {row_number} contains an invalid field value.")
        self.row_number = row_number


@dataclass
class ImportOutcome:
    imported_ids: list[str] = field(default_factory=list)
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0


_SCALAR_TYPES = (str, int, float, type(None))
_PASSTHROUGH_FIELDS = (
    "key_label",
    "custom_base_url",
    "status",
    "credits_remaining",
    "credits_total",
    "credits_used",
    "health_status",
    "usability_status",
    "usability_note",
    "last_checked_at",
    "last_error",
)


def _prepare_import_row(row: dict[str, Any], row_number: int) -> dict[str, Any] | None:
    key_value = row.get("key_value") or row.get("key")
    if not key_value:
        return None
    if not isinstance(key_value, str):
        raise ImportRowError(row_number)
    provider_id = row.get("provider_id") or detect_provider_from_key(key_value) or "unidentified"
    source_node = row.get("node_id") or row.get("source_node")
    try:
        priority = int(row.get("priority") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ImportRowError(row_number) from exc
    record: dict[str, Any] = {
        "provider_id": str(provider_id),
        "key_value": key_value,
        "source_node": source_node,
        "priority": priority,
        "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else None,
        "is_valid": int(bool(row.get("is_valid"))),
        "is_usable": int(bool(row.get("is_usable"))),
    }
    for name in _PASSTHROUGH_FIELDS:
        record[name] = row.get(name)
    if record["status"] is None:
        record["status"] = "pending_validation"
    for name, value in record.items():
        if name != "metadata" and not isinstance(value, _SCALAR_TYPES):
            raise ImportRowError(row_number)
        if isinstance(value, int) and not -(2**63) <= value < 2**63:
            raise ImportRowError(row_number)
    return record


async def import_dashboard_rows_with_ids(
    db_path: Path,
    rows: list[dict[str, Any]],
    *,
    dry_run: bool,
    reset_validation: bool = False,
) -> ImportOutcome:
    outcome = ImportOutcome()
    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        record = _prepare_import_row(row, index)
        if record is None:
            outcome.skipped += 1
            continue
        if reset_validation:
            record.update(status="pending_validation", is_valid=0, is_usable=0, last_error=None)
        records.append(record)
    if dry_run:
        outcome.imported = len(records)
        return outcome

    await init_db(db_path)
    outcome.imported_ids, outcome.duplicates = await import_upstream_keys_atomic(db_path, records)
    outcome.imported = len(outcome.imported_ids)
    return outcome


async def import_dashboard_export(db_path: Path, export_path: Path, *, dry_run: bool) -> int:
    rows = load_export_payload(export_path)
    return await import_dashboard_rows(db_path, rows, dry_run=dry_run)


async def import_dashboard_json(db_path: Path, data: bytes, *, dry_run: bool) -> int:
    outcome = await import_dashboard_json_with_ids(db_path, data, dry_run=dry_run)
    return outcome.imported


async def import_dashboard_json_with_ids(
    db_path: Path,
    data: bytes,
    *,
    dry_run: bool,
    reset_validation: bool = False,
) -> ImportOutcome:
    payload = json.loads(data)
    rows = _parse_export_payload(payload)
    return await import_dashboard_rows_with_ids(
        db_path, rows, dry_run=dry_run, reset_validation=reset_validation
    )

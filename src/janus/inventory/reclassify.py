from __future__ import annotations

from pathlib import Path
from typing import Any

from janus.inventory.key_checker import check_upstream_key
from janus.inventory.provider_detection import resolve_provider_for_key
from janus.inventory.xiaomi_tokenplan import TOKENPLAN_PROVIDER_ID
from janus.storage.upstream_keys import list_upstream_keys, update_upstream_key


async def reclassify_upstream_keys(
    db_path: str | Path,
    *,
    dry_run: bool = True,
    scope: str = "invalid",
) -> dict[str, Any]:
    keys = await list_upstream_keys(db_path)
    if scope == "all":
        candidates = [key for key in keys if key.get("status") != "revoked"]
    else:
        candidates = [key for key in keys if key.get("status") == "invalid"]

    moved: list[dict[str, str]] = []
    region_fixed: list[dict[str, str]] = []
    unchanged: list[str] = []

    for key in candidates:
        key_value = str(key["key_value"])
        existing_base = key.get("custom_base_url")
        found, meta = await resolve_provider_for_key(
            key_value,
            chosen_provider="auto",
            custom_base_url=existing_base,
        )
        new_base = None
        if meta and meta.get("custom_base_url"):
            new_base = str(meta["custom_base_url"]).rstrip("/")

        changed_provider = found and found != key["provider_id"]
        changed_region = bool(new_base and new_base != (existing_base or None))

        if not changed_provider and not changed_region:
            # For tokenplan invalid keys, still re-check with region discovery
            if (
                key.get("provider_id") == TOKENPLAN_PROVIDER_ID
                or key_value.startswith("tp-")
            ) and key.get("status") == "invalid":
                if not dry_run:
                    await update_upstream_key(
                        db_path,
                        key["id"],
                        {
                            "provider_id": TOKENPLAN_PROVIDER_ID,
                            "custom_base_url": new_base or existing_base,
                            "metadata": meta,
                            "status": "pending_validation",
                            "last_error": None,
                        },
                    )
                    await check_upstream_key(db_path, key["id"])
                region_fixed.append(
                    {
                        "id": key["id"],
                        "key_masked": key["key_masked"],
                        "provider_id": TOKENPLAN_PROVIDER_ID,
                        "base_url": new_base or existing_base or "",
                    }
                )
            else:
                unchanged.append(key["id"])
            continue

        entry = {
            "id": key["id"],
            "key_masked": key["key_masked"],
            "from": key["provider_id"],
            "to": found,
            "base_url": new_base or existing_base or "",
        }
        if changed_provider:
            moved.append(entry)
        else:
            region_fixed.append(entry)

        if not dry_run:
            await update_upstream_key(
                db_path,
                key["id"],
                {
                    "provider_id": found,
                    "custom_base_url": new_base or existing_base,
                    "metadata": meta,
                    "status": "pending_validation",
                    "is_valid": 0,
                    "is_usable": 0,
                    "usability_status": "unknown",
                    "last_error": None,
                },
            )
            await check_upstream_key(db_path, key["id"])

    return {
        "scanned": len(candidates),
        "moved": moved,
        "region_fixed": region_fixed,
        "unchanged": len(unchanged),
        "dry_run": dry_run,
    }

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from janus.inventory.key_checker import check_upstream_key, validate_key
from janus.inventory.provider_detection import detectable_provider_ids
from janus.inventory.url_guard import detect_provider_from_key
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
    unchanged: list[str] = []

    for key in candidates:
        guess = detect_provider_from_key(key["key_value"])
        order = [guess, *detectable_provider_ids(guess)] if guess else detectable_provider_ids(None)
        found: str | None = None
        for provider_id in order:
            try:
                result = await validate_key(key["key_value"], provider_id, skip_probe=True)
            except Exception:
                continue
            if result.get("is_valid"):
                found = provider_id
                break

        if found and found != key["provider_id"]:
            moved.append(
                {
                    "id": key["id"],
                    "key_masked": key["key_masked"],
                    "from": key["provider_id"],
                    "to": found,
                }
            )
            if not dry_run:
                await update_upstream_key(
                    db_path,
                    key["id"],
                    {
                        "provider_id": found,
                        "status": "pending_validation",
                        "last_error": None,
                    },
                )
                asyncio.create_task(check_upstream_key(db_path, key["id"]))
        else:
            unchanged.append(key["id"])

    return {
        "dry_run": dry_run,
        "scope": scope,
        "examined": len(candidates),
        "moved": moved,
        "unchanged_count": len(unchanged),
    }

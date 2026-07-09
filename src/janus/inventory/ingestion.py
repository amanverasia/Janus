from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from janus.inventory.catalog import get_inventory_provider
from janus.inventory.provider_detection import resolve_provider_for_key
from janus.inventory.url_guard import is_http_url, mask_key
from janus.storage.upstream_keys import (
    create_upstream_key,
    find_upstream_key_by_value,
    find_upstream_key_by_value_and_provider,
    update_upstream_key,
)

MIN_KEY_LENGTH = int(os.environ.get("INVENTORY_MIN_KEY_LENGTH", "16"))
MAX_KEY_LENGTH = int(os.environ.get("INVENTORY_MAX_KEY_LENGTH", "512"))
MAX_SUBMIT_BATCH = int(os.environ.get("INVENTORY_MAX_SUBMIT_BATCH", "200"))
_NON_KEY_PATTERN = re.compile(r"^(https?://|/|\.|\d+$)")


@dataclass
class KeyIngestEntry:
    key: str
    label: str | None = None
    provider: str | None = None
    base_url: str | None = None
    source_node: str | None = None


IngestStatus = Literal["registered", "exists", "rejected", "updated", "skipped", "unidentified"]


def validate_key_value(key_value: str) -> str | None:
    cleaned = key_value.strip().replace("\r", "").replace("\n", "").replace("\t", "")
    if not cleaned:
        return "Key is missing"
    if len(cleaned) < MIN_KEY_LENGTH:
        return f"Key too short (min {MIN_KEY_LENGTH} chars)"
    if len(cleaned) > MAX_KEY_LENGTH:
        return f"Key too long (max {MAX_KEY_LENGTH} chars)"
    if _NON_KEY_PATTERN.match(cleaned):
        return "Does not look like an API key"
    return None


def enforce_batch_size(count: int) -> str | None:
    if count > MAX_SUBMIT_BATCH:
        return f"Too many keys ({count}); max {MAX_SUBMIT_BATCH} per request"
    return None


async def ingest_upstream_key(
    db_path: str | Path,
    entry: KeyIngestEntry,
    *,
    chosen_provider: str = "auto",
    custom_base_url: str | None = None,
    require_provider: bool = False,
) -> dict[str, Any]:
    validation_error = validate_key_value(entry.key)
    if validation_error:
        return {
            "key_masked": entry.key[:8] + "…" if entry.key else "?",
            "label": entry.label,
            "status": "rejected",
            "error": validation_error,
        }

    key_value = entry.key.strip().replace("\r", "").replace("\n", "").replace("\t", "")
    key_masked = mask_key(key_value)
    base_url = (entry.base_url or custom_base_url or "").strip() or None
    if base_url and not is_http_url(base_url):
        return {
            "key_masked": key_masked,
            "label": entry.label,
            "status": "rejected",
            "error": "base_url must be a valid http(s) URL",
        }

    existing = await find_upstream_key_by_value(db_path, key_value)
    if existing and existing["provider_id"] != "unidentified":
        provider = get_inventory_provider(existing["provider_id"])
        display_name = provider["display_name"] if provider else existing["provider_id"]
        return {
            "id": existing["id"],
            "key_masked": existing["key_masked"],
            "label": entry.label or existing.get("key_label"),
            "provider_id": existing["provider_id"],
            "provider_display_name": display_name,
            "status": "exists",
        }

    provider_choice = entry.provider or chosen_provider
    if provider_choice and provider_choice != "auto":
        if get_inventory_provider(provider_choice) is None:
            return {
                "key_masked": key_masked,
                "label": entry.label,
                "status": "rejected",
                "error": f"Unknown provider: {provider_choice}",
            }
        resolved_provider = provider_choice
        custom_meta = None
        if provider_choice == "custom" and base_url:
            custom_meta = {"custom_base_url": base_url.rstrip("/")}
    else:
        resolved_provider, custom_meta = await resolve_provider_for_key(
            key_value,
            chosen_provider="auto",
            custom_base_url=base_url,
        )
        if require_provider and resolved_provider in {"unidentified", None}:
            return {
                "key_masked": key_masked,
                "label": entry.label,
                "status": "rejected",
                "error": "Cannot detect provider. Pass provider field.",
            }

    effective_base_url = base_url
    if resolved_provider == "custom":
        effective_base_url = (custom_meta or {}).get("custom_base_url") or base_url
    elif custom_meta and custom_meta.get("custom_base_url"):
        # Token Plan (and similar) region endpoints discovered during detection.
        effective_base_url = str(custom_meta["custom_base_url"]).rstrip("/")
    if resolved_provider == "custom" and not effective_base_url:
        return {
            "key_masked": key_masked,
            "label": entry.label,
            "provider_id": resolved_provider,
            "status": "rejected",
            "error": "Custom provider requires a base URL.",
        }

    is_unidentified = resolved_provider == "unidentified"
    if existing and existing["provider_id"] == "unidentified":
        await update_upstream_key(
            db_path,
            existing["id"],
            {
                "provider_id": resolved_provider,
                "custom_base_url": (
                    effective_base_url
                    if (resolved_provider == "custom" or effective_base_url)
                    else None
                ),
                "metadata": custom_meta,
                "status": "unidentified" if is_unidentified else "pending_validation",
                "is_valid": 0,
                "health_status": "healthy",
                "usability_status": "unknown",
                "usability_note": None,
                "last_error": (
                    "Provider not auto-detected — needs manual review" if is_unidentified else None
                ),
            },
        )
        return {
            "id": existing["id"],
            "key_masked": existing["key_masked"],
            "label": entry.label or existing.get("key_label"),
            "provider_id": resolved_provider,
            "status": "updated" if not is_unidentified else "unidentified",
        }

    duplicate = await find_upstream_key_by_value_and_provider(db_path, key_value, resolved_provider)
    if duplicate:
        provider = get_inventory_provider(resolved_provider)
        return {
            "id": duplicate["id"],
            "key_masked": duplicate["key_masked"],
            "label": entry.label or duplicate.get("key_label"),
            "provider_id": resolved_provider,
            "provider_display_name": provider["display_name"] if provider else resolved_provider,
            "status": "exists",
        }

    # Persist regional base URLs for Token Plan etc., not only custom providers.
    persist_base = effective_base_url if (
        resolved_provider == "custom" or effective_base_url
    ) else None
    record = await create_upstream_key(
        db_path,
        provider_id=resolved_provider,
        key_value=key_value,
        key_label=entry.label,
        custom_base_url=persist_base,
        source_node=entry.source_node,
        metadata=custom_meta,
    )
    if is_unidentified:
        await update_upstream_key(
            db_path,
            record["id"],
            {
                "status": "unidentified",
                "last_error": "Provider not auto-detected — needs manual review",
            },
        )
    return {
        "id": record["id"],
        "key_masked": record["key_masked"],
        "label": entry.label,
        "provider_id": resolved_provider,
        "status": "unidentified" if is_unidentified else "registered",
    }

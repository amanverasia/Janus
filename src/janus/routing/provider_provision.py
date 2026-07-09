from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from janus.catalog import inventory_to_gateway_map
from janus.dashboard.catalog import CATALOG
from janus.inventory.catalog import get_inventory_provider
from janus.inventory.ingestion import KeyIngestEntry, validate_key_value
from janus.inventory.url_guard import detect_provider_from_key, mask_key
from janus.storage.providers_db import (
    create_provider,
    get_provider,
    list_providers,
    toggle_provider,
)

INVENTORY_TO_CATALOG: dict[str, str] = inventory_to_gateway_map()

NON_ROUTABLE = frozenset({"unidentified"})

ProvisionAction = Literal["exists", "create", "enable", "skip"]


@dataclass(frozen=True)
class ProvisionGroup:
    inventory_provider_id: str
    display_name: str
    key_count: int
    sample_masked: list[str]
    routing_catalog_id: str | None
    routing_prefix: str
    existing_provider_id: str | None
    existing_enabled: bool
    action: ProvisionAction
    skip_reason: str | None


@dataclass(frozen=True)
class ProvisionPreview:
    groups: list[ProvisionGroup]
    rejected_count: int
    unidentified_count: int
    needs_confirmation: bool


def routing_catalog_id_for_inventory(inventory_provider_id: str) -> str | None:
    if inventory_provider_id in NON_ROUTABLE:
        return None
    if inventory_provider_id == "custom":
        return "custom"
    mapped = INVENTORY_TO_CATALOG.get(inventory_provider_id, inventory_provider_id)
    if mapped in CATALOG:
        return mapped
    return None


def detect_inventory_provider_for_key(
    key: str,
    *,
    chosen_provider: str = "auto",
) -> str:
    if chosen_provider != "auto":
        return chosen_provider
    return detect_provider_from_key(key) or "unidentified"


def build_provision_preview(
    entries: list[KeyIngestEntry],
    *,
    chosen_provider: str = "auto",
    existing_providers: list[dict[str, Any]] | None = None,
) -> ProvisionPreview:
    by_prefix: dict[str, str] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in existing_providers or []:
        prefix = str(row.get("prefix") or "")
        if prefix:
            by_prefix[prefix] = str(row["id"])
        by_id[str(row["id"])] = row

    grouped: dict[str, list[str]] = {}
    rejected_count = 0
    for entry in entries:
        if validate_key_value(entry.key):
            rejected_count += 1
            continue
        provider_id = entry.provider or detect_inventory_provider_for_key(
            entry.key.strip(),
            chosen_provider=chosen_provider,
        )
        grouped.setdefault(provider_id, []).append(mask_key(entry.key.strip()))

    groups: list[ProvisionGroup] = []
    unidentified_count = len(grouped.get("unidentified", []))

    for inventory_id in sorted(grouped.keys()):
        keys = grouped[inventory_id]
        inv = get_inventory_provider(inventory_id)
        display_name = inv["display_name"] if inv else inventory_id
        catalog_id = routing_catalog_id_for_inventory(inventory_id)

        if catalog_id is None:
            groups.append(
                ProvisionGroup(
                    inventory_provider_id=inventory_id,
                    display_name=display_name,
                    key_count=len(keys),
                    sample_masked=keys[:3],
                    routing_catalog_id=None,
                    routing_prefix="",
                    existing_provider_id=None,
                    existing_enabled=False,
                    action="skip",
                    skip_reason="No routing template — keys go to inventory only",
                )
            )
            continue

        catalog = CATALOG[catalog_id]
        prefix = str(catalog.get("prefix") or catalog_id)
        existing_id = by_prefix.get(prefix) or (catalog_id if catalog_id in by_id else None)
        existing_row = by_id.get(existing_id) if existing_id else None
        enabled = bool(existing_row and existing_row.get("is_enabled"))

        if existing_row:
            action: ProvisionAction = "exists" if enabled else "enable"
            groups.append(
                ProvisionGroup(
                    inventory_provider_id=inventory_id,
                    display_name=display_name,
                    key_count=len(keys),
                    sample_masked=keys[:3],
                    routing_catalog_id=catalog_id,
                    routing_prefix=prefix,
                    existing_provider_id=existing_id,
                    existing_enabled=enabled,
                    action=action,
                    skip_reason=None,
                )
            )
        else:
            groups.append(
                ProvisionGroup(
                    inventory_provider_id=inventory_id,
                    display_name=display_name,
                    key_count=len(keys),
                    sample_masked=keys[:3],
                    routing_catalog_id=catalog_id,
                    routing_prefix=prefix,
                    existing_provider_id=None,
                    existing_enabled=False,
                    action="create",
                    skip_reason=None,
                )
            )

    needs_confirmation = (
        rejected_count > 0
        or unidentified_count > 0
        or len(groups) > 1
        or any(g.action in {"create", "enable"} for g in groups)
    )
    return ProvisionPreview(
        groups=groups,
        rejected_count=rejected_count,
        unidentified_count=unidentified_count,
        needs_confirmation=needs_confirmation,
    )


async def ensure_routing_providers(
    db_path: str | Path,
    inventory_provider_ids: set[str],
    *,
    custom_base_url: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for inventory_id in sorted(inventory_provider_ids):
        catalog_id = routing_catalog_id_for_inventory(inventory_id)
        if catalog_id is None:
            continue

        catalog = CATALOG[catalog_id]
        prefix = str(catalog.get("prefix") or catalog_id)
        providers = await list_providers(db_path)
        existing = next(
            (p for p in providers if p.get("prefix") == prefix or p["id"] == catalog_id),
            None,
        )

        if existing:
            if not existing.get("is_enabled"):
                await toggle_provider(db_path, str(existing["id"]))
            results.append(
                {
                    "inventory_provider_id": inventory_id,
                    "provider_id": existing["id"],
                    "action": "enabled" if not existing.get("is_enabled") else "exists",
                    "prefix": prefix,
                }
            )
            continue

        if catalog_id == "custom":
            base_url = (custom_base_url or "").strip()
            if not base_url:
                results.append(
                    {
                        "inventory_provider_id": inventory_id,
                        "action": "skipped",
                        "reason": "Custom routing requires a base URL",
                    }
                )
                continue
            provider_id = "custom"
        else:
            base_url = str(catalog.get("base_url") or "")
            provider_id = catalog_id

        if await get_provider(db_path, provider_id):
            results.append(
                {
                    "inventory_provider_id": inventory_id,
                    "provider_id": provider_id,
                    "action": "exists",
                    "prefix": prefix,
                }
            )
            continue

        await create_provider(
            db_path,
            {
                "id": provider_id,
                "prefix": prefix,
                "api_type": catalog["api_type"],
                "base_url": base_url,
                "api_key": None,
                "models": list(catalog.get("default_models") or []),
                "transports": catalog.get("transports"),
            },
        )
        results.append(
            {
                "inventory_provider_id": inventory_id,
                "provider_id": provider_id,
                "action": "created",
                "prefix": prefix,
            }
        )
    return results

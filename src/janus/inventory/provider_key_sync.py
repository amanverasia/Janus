from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from janus.inventory.catalog import get_inventory_provider
from janus.inventory.key_encryption import hash_upstream_key
from janus.inventory.recheck_scheduler import schedule_upstream_recheck
from janus.routing.inventory_bridge import inventory_provider_id_for_prefix
from janus.storage.database import get_connection
from janus.storage.upstream_keys import (
    create_upstream_key,
    find_upstream_key_by_value,
    update_upstream_key,
)

logger = logging.getLogger(__name__)

_MIRROR_SOURCE_PREFIX = "gateway:"


def _source_node(provider_id: str) -> str:
    return f"{_MIRROR_SOURCE_PREFIX}{provider_id}"


async def _find_mirrored_key(db_path: str | Path, provider_id: str) -> dict[str, Any] | None:
    source = _source_node(provider_id)
    async with get_connection(db_path) as db:
        async with db.execute(
            "SELECT * FROM upstream_keys WHERE source_node = ? LIMIT 1",
            (source,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


async def _resolve_inventory_provider_id(db_path: str | Path, provider: dict[str, Any]) -> str:
    prefix = str(provider.get("prefix") or "")
    inventory_id = inventory_provider_id_for_prefix(prefix)
    if get_inventory_provider(inventory_id) is not None:
        return inventory_id
    await _upsert_custom_inventory_provider(db_path, provider, inventory_id)
    return inventory_id


async def _upsert_custom_inventory_provider(
    db_path: str | Path, provider: dict[str, Any], inventory_id: str
) -> None:
    display_name = str(provider.get("id") or inventory_id)
    base_url = str(provider.get("base_url") or "").rstrip("/")
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO inventory_providers
               (id, name, display_name, base_url, auth_type, auth_header, auth_prefix,
                billing_model, is_direct, routing_note, updated_at)
               VALUES (?, ?, ?, ?, 'api_key', 'Authorization', 'Bearer',
                       'unknown', 1, 'Mirrored from Providers page', datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 display_name = excluded.display_name,
                 base_url = excluded.base_url,
                 updated_at = datetime('now')""",
            (inventory_id, inventory_id, display_name, base_url),
        )
        await db.commit()


async def sync_provider_key(
    db_path: str | Path,
    *,
    provider: dict[str, Any],
    schedule_recheck: bool = True,
) -> str | None:
    api_key = provider.get("api_key")
    if not isinstance(api_key, str) or not api_key:
        await delete_mirrored_provider_key(db_path, str(provider.get("id") or ""))
        return None

    provider_id = str(provider.get("id") or "")
    if not provider_id:
        return None

    key_value = api_key.strip()
    key_hash = hash_upstream_key(key_value)
    existing = await _find_mirrored_key(db_path, provider_id)
    if existing is not None and existing.get("key_hash") == key_hash:
        return str(existing["id"])

    inventory_id = await _resolve_inventory_provider_id(db_path, provider)

    duplicate = await find_upstream_key_by_value(db_path, key_value)
    if (
        duplicate is not None
        and duplicate.get("id") != (existing or {}).get("id")
        and duplicate.get("provider_id") != "unidentified"
    ):
        logger.info(
            "Skipping mirror of provider %s key — already present in inventory under %s",
            provider_id,
            duplicate.get("provider_id"),
        )
        if existing is not None:
            await update_upstream_key(db_path, str(existing["id"]), {"status": "revoked"})
        return None

    base_url = (
        (str(provider.get("base_url") or "").rstrip("/") or None)
        if inventory_id
        not in {
            "openai",
            "anthropic",
            "gemini",
            "google",
        }
        else None
    )
    label = f"via Providers: {provider_id}"

    if existing is not None:
        await update_upstream_key(
            db_path,
            str(existing["id"]),
            {
                "key_value": key_value,
                "provider_id": inventory_id,
                "custom_base_url": base_url,
                "key_label": label,
                "status": "pending_validation",
                "is_valid": 0,
                "is_usable": 0,
                "usability_status": "unknown",
                "usability_note": None,
                "last_error": None,
                "consecutive_failures": 0,
                "validation_paused_at": None,
            },
        )
        key_id = str(existing["id"])
    else:
        record = await create_upstream_key(
            db_path,
            provider_id=inventory_id,
            key_value=key_value,
            key_label=label,
            custom_base_url=base_url,
            source_node=_source_node(provider_id),
        )
        key_id = str(record["id"])

    if schedule_recheck:
        schedule_upstream_recheck(key_id, db_path)
    return key_id


async def delete_mirrored_provider_key(db_path: str | Path, provider_id: str) -> None:
    if not provider_id:
        return
    existing = await _find_mirrored_key(db_path, provider_id)
    if existing is None:
        return
    await update_upstream_key(db_path, str(existing["id"]), {"status": "revoked"})


async def backfill_provider_keys(db_path: str | Path) -> int:
    from janus.storage.providers_db import list_providers

    mirrored = 0
    for provider in await list_providers(db_path):
        api_key = provider.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            continue
        existing = await _find_mirrored_key(db_path, str(provider["id"]))
        if existing is not None:
            continue
        try:
            key_id = await sync_provider_key(db_path, provider=provider, schedule_recheck=True)
            if key_id is not None:
                mirrored += 1
        except Exception:
            logger.exception("Backfill mirror failed for provider %s", provider.get("id"))
    return mirrored

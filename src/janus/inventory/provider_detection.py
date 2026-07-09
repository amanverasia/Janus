from __future__ import annotations

import asyncio
import os
from typing import Any

from janus.inventory.catalog import get_inventory_providers
from janus.inventory.key_checker import validate_key
from janus.inventory.xiaomi_tokenplan import (
    TOKENPLAN_PROVIDER_ID,
    XIAOMI_PAYGO_PROVIDER_ID,
    metadata_with_region,
    region_id_for_base_url,
)

DETECT_CONCURRENCY = int(os.environ.get("DETECT_CONCURRENCY", "6"))


def detectable_provider_ids(exclude_id: str | None = None) -> list[str]:
    excluded = {"custom", "unidentified", "openrouter"}
    if exclude_id:
        excluded.add(exclude_id)
    return [
        provider["id"]
        for provider in get_inventory_providers().values()
        if provider["id"] not in excluded
        and provider.get("health_check_endpoint")
        and provider.get("base_url")
    ]


async def find_authenticating_provider(
    key_value: str,
    candidate_ids: list[str],
    *,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    if not candidate_ids:
        return None

    index = 0
    lock = asyncio.Lock()
    found: str | None = None
    found_rank = len(candidate_ids)

    async def worker() -> None:
        nonlocal index, found, found_rank
        while True:
            async with lock:
                if found is not None or index >= len(candidate_ids):
                    return
                rank = index
                index += 1
                provider_id = candidate_ids[rank]
            try:
                result = await validate_key(
                    key_value,
                    provider_id,
                    metadata,
                    skip_probe=True,
                )
            except Exception:
                continue
            if not result.get("is_valid"):
                continue
            async with lock:
                if rank < found_rank:
                    found = provider_id
                    found_rank = rank

    pool_size = min(DETECT_CONCURRENCY, len(candidate_ids))
    await asyncio.gather(*(worker() for _ in range(pool_size)))
    return found


async def resolve_provider_for_key(
    key_value: str,
    *,
    chosen_provider: str = "auto",
    custom_base_url: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    from janus.inventory.url_guard import detect_provider_from_key

    custom_meta: dict[str, Any] | None = None
    if chosen_provider != "auto":
        provider_id = chosen_provider
        if provider_id == "custom" and custom_base_url:
            custom_meta = {"custom_base_url": custom_base_url.rstrip("/")}
        elif provider_id == TOKENPLAN_PROVIDER_ID and custom_base_url:
            region = region_id_for_base_url(custom_base_url) or "custom"
            custom_meta = metadata_with_region(
                None, region=region, base_url=custom_base_url.rstrip("/")
            )
        return provider_id, custom_meta

    guess = detect_provider_from_key(key_value)

    # Token Plan keys must never fall through to pay-as-you-go Xiaomi.
    # Region is discovered during validate_key and returned as metadata.
    if guess == TOKENPLAN_PROVIDER_ID or key_value.startswith("tp-"):
        seed_meta: dict[str, Any] | None = None
        if custom_base_url:
            region = region_id_for_base_url(custom_base_url) or "custom"
            seed_meta = metadata_with_region(
                None, region=region, base_url=custom_base_url.rstrip("/")
            )
        result = await validate_key(
            key_value,
            TOKENPLAN_PROVIDER_ID,
            seed_meta,
            skip_probe=True,
        )
        if result.get("is_valid"):
            meta = dict(result.get("metadata") or {})
            if result.get("custom_base_url"):
                meta["custom_base_url"] = str(result["custom_base_url"]).rstrip("/")
            if result.get("tokenplan_region"):
                meta["tokenplan_region"] = result["tokenplan_region"]
            return TOKENPLAN_PROVIDER_ID, meta or seed_meta
        # Still label as tokenplan even if all regions fail — never xiaomi paygo.
        return TOKENPLAN_PROVIDER_ID, seed_meta

    order = [guess, *detectable_provider_ids(guess)] if guess else detectable_provider_ids(None)
    # Drop paygo xiaomi when we already know this isn't a tokenplan key? keep normal.
    # But never prefer xiaomi for non-tp keys unless it authenticates.
    confirmed = await find_authenticating_provider(key_value, order)
    if confirmed:
        # Guard: if something authenticated as xiaomi paygo but key is tp-, rewrite
        if confirmed == XIAOMI_PAYGO_PROVIDER_ID and key_value.startswith("tp-"):
            return TOKENPLAN_PROVIDER_ID, None
        return confirmed, None

    if custom_base_url:
        custom_meta = {"custom_base_url": custom_base_url.rstrip("/")}
        try:
            result = await validate_key(key_value, "custom", custom_meta, skip_probe=True)
        except Exception:
            result = {"is_valid": False}
        if result.get("is_valid"):
            return "custom", custom_meta

    return confirmed or guess or "unidentified", custom_meta

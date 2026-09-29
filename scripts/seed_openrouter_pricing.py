#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import httpx

from janus.storage.database import init_db
from janus.storage.pricing_db import (
    create_or_update_pricing_override,
    delete_pricing_override,
    list_pricing_overrides,
)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _per_mtok(value: str | float | None) -> float | None:
    if value is None:
        return 0.0
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return None


def _to_override(model: dict[str, Any]) -> dict[str, float | str] | None:
    pricing = model.get("pricing") or {}
    model_id = model.get("id")
    if not isinstance(model_id, str):
        return None
    rates: dict[str, float] = {}
    for field, key in (
        ("input_per_mtok", "prompt"),
        ("output_per_mtok", "completion"),
        ("cache_creation_per_mtok", "input_cache_write"),
        ("cache_read_per_mtok", "input_cache_read"),
    ):
        rate = _per_mtok(pricing.get(key))
        if rate is None:
            print(f"Skipping {model_id}: unparseable {key} pricing {pricing.get(key)!r}")
            return None
        rates[field] = rate
    if rates["input_per_mtok"] == 0.0 and rates["output_per_mtok"] == 0.0:
        return None
    return {"model": model_id, **rates}


async def _prune_stale(db_path: Path, upstream_ids: set[str], *, dry_run: bool) -> int:
    overrides = await list_pricing_overrides(db_path)
    stale = sorted(
        str(row["model"])
        for row in overrides
        if "/" in str(row["model"]) and str(row["model"]) not in upstream_ids
    )
    verb = "Would prune" if dry_run else "Pruned"
    for model in stale:
        if not dry_run:
            await delete_pricing_override(db_path, model)
        print(f"{verb} stale OpenRouter pricing override: {model}")
    return len(stale)


async def seed(db_path: Path, *, dry_run: bool, prune: bool) -> tuple[int, int, int]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(OPENROUTER_MODELS_URL)
        resp.raise_for_status()
        models = resp.json().get("data", [])

    upstream_ids: set[str] = set()
    for model in models:
        model_id = model.get("id")
        if isinstance(model_id, str):
            upstream_ids.add(model_id)

    await init_db(db_path)
    written = 0
    for model in models:
        override = _to_override(model)
        if override is None:
            continue
        if not dry_run:
            await create_or_update_pricing_override(db_path, override)
        written += 1
    pruned = 0
    if prune:
        pruned = await _prune_stale(db_path, upstream_ids, dry_run=dry_run)
    return written, pruned, len(models)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed pricing overrides from the OpenRouter models catalog"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".janus" / "janus.db",
        help="Path to Janus SQLite database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and count without writing to the database",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=(
            "Also delete pricing overrides for OpenRouter-style model IDs "
            "(vendor/model) that are no longer in the upstream catalog"
        ),
    )
    args = parser.parse_args()

    written, pruned, total = asyncio.run(seed(args.db, dry_run=args.dry_run, prune=args.prune))
    verb = "Would seed" if args.dry_run else "Seeded"
    message = f"{verb} {written} priced models (of {total} in OpenRouter catalog) into {args.db}"
    if args.prune:
        message += f", {'would prune' if args.dry_run else 'pruned'} {pruned} stale overrides"
    print(message)


if __name__ == "__main__":
    main()

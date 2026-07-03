#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import httpx

from janus.storage.database import init_db
from janus.storage.pricing_db import create_or_update_pricing_override

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _per_mtok(value: str | float | None) -> float:
    if value is None:
        return 0.0
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return 0.0


def _to_override(model: dict[str, Any]) -> dict[str, float | str] | None:
    pricing = model.get("pricing") or {}
    model_id = model.get("id")
    if not isinstance(model_id, str):
        return None
    input_rate = _per_mtok(pricing.get("prompt"))
    output_rate = _per_mtok(pricing.get("completion"))
    if input_rate == 0.0 and output_rate == 0.0:
        return None
    return {
        "model": model_id,
        "input_per_mtok": input_rate,
        "output_per_mtok": output_rate,
        "cache_creation_per_mtok": _per_mtok(pricing.get("input_cache_write")),
        "cache_read_per_mtok": _per_mtok(pricing.get("input_cache_read")),
    }


async def seed(db_path: Path, *, dry_run: bool) -> tuple[int, int]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(OPENROUTER_MODELS_URL)
        resp.raise_for_status()
        models = resp.json().get("data", [])

    await init_db(db_path)
    written = 0
    for model in models:
        override = _to_override(model)
        if override is None:
            continue
        if not dry_run:
            await create_or_update_pricing_override(db_path, override)
        written += 1
    return written, len(models)


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
    args = parser.parse_args()

    written, total = asyncio.run(seed(args.db, dry_run=args.dry_run))
    verb = "Would seed" if args.dry_run else "Seeded"
    print(f"{verb} {written} priced models (of {total} in OpenRouter catalog) into {args.db}")


if __name__ == "__main__":
    main()

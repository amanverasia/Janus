from __future__ import annotations

import asyncio
import os
from pathlib import Path

CHECK_INTERVAL_HOURS = float(os.environ.get("INVENTORY_CHECK_INTERVAL_HOURS", "12"))


async def run_inventory_scheduler(db_path: Path, stop_event: asyncio.Event) -> None:
    from janus.inventory.key_checker import check_all_upstream_keys

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=CHECK_INTERVAL_HOURS * 3600)
            return
        except TimeoutError:
            await check_all_upstream_keys(db_path)


def scheduler_enabled() -> bool:
    return os.environ.get("INVENTORY_SCHEDULER_ENABLED", "true").lower() != "false"

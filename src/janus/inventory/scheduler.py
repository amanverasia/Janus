from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

CHECK_INTERVAL_HOURS = float(os.environ.get("INVENTORY_CHECK_INTERVAL_HOURS", "12"))
MIN_CHECK_INTERVAL_HOURS = 1.0

logger = logging.getLogger(__name__)


def _interval_seconds() -> float:
    return max(CHECK_INTERVAL_HOURS, MIN_CHECK_INTERVAL_HOURS) * 3600


async def run_inventory_scheduler(db_path: Path, stop_event: asyncio.Event) -> None:
    from janus.inventory.key_checker import check_all_upstream_keys

    while not stop_event.is_set():
        try:
            await check_all_upstream_keys(db_path)
        except Exception:
            logger.exception("Scheduled inventory key check failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_interval_seconds())
            return
        except TimeoutError:
            continue


def scheduler_enabled() -> bool:
    return os.environ.get("INVENTORY_SCHEDULER_ENABLED", "true").lower() != "false"

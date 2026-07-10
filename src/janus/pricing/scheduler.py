from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 24.0


def sync_interval_hours() -> float:
    return float(os.environ.get("PRICING_SYNC_INTERVAL_HOURS", str(DEFAULT_INTERVAL_HOURS)))


def pricing_scheduler_enabled() -> bool:
    return os.environ.get("PRICING_SYNC_ENABLED", "true").lower() != "false"


async def run_pricing_scheduler(app: FastAPI, stop_event: asyncio.Event) -> None:
    from janus.dashboard.reload import reload_pricing
    from janus.pricing.sync import PricingSyncError, fetch_and_sync

    db_path: Path = app.state.db_path

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sync_interval_hours() * 3600)
            return
        except TimeoutError:
            try:
                await fetch_and_sync(db_path)
                await reload_pricing(app)
            except PricingSyncError as exc:
                logger.warning("Scheduled pricing sync failed: %s", exc)
            except Exception:
                logger.exception("Scheduled pricing sync raised an unexpected error")

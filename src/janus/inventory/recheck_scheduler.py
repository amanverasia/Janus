from __future__ import annotations

import asyncio
from pathlib import Path

from janus.background import spawn_background
from janus.inventory.key_checker import check_upstream_key
from janus.storage.upstream_keys import update_upstream_key


def schedule_upstream_recheck(key_id: str, db_path: str | Path) -> asyncio.Task[None]:
    async def _run() -> None:
        await update_upstream_key(
            db_path,
            key_id,
            {
                "status": "pending_validation",
                "last_error": None,
                "consecutive_failures": 0,
                "validation_paused_at": None,
            },
        )
        await check_upstream_key(db_path, key_id)

    return spawn_background(_run(), name=f"inventory-recheck:{key_id}")

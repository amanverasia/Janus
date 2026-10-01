from __future__ import annotations

import logging
from pathlib import Path

from janus.storage.upstream_keys import get_upstream_key, swap_upstream_key_value

logger = logging.getLogger(__name__)


class SqliteCredentialStore:
    def __init__(self, db_path: str | Path, upstream_key_id: str) -> None:
        self.db_path = db_path
        self.upstream_key_id = upstream_key_id

    async def load(self) -> str | None:
        try:
            row = await get_upstream_key(self.db_path, self.upstream_key_id)
        except Exception:
            logger.warning("Could not load credential for upstream key %s", self.upstream_key_id)
            return None
        if row is None or row.get("status") == "revoked":
            return None
        value = row.get("key_value")
        return value if isinstance(value, str) and value else None

    async def save(self, previous: str, current: str) -> bool:
        try:
            return await swap_upstream_key_value(
                self.db_path, self.upstream_key_id, previous=previous, current=current
            )
        except Exception:
            logger.warning(
                "Could not persist refreshed credential for upstream key %s",
                self.upstream_key_id,
            )
            return False

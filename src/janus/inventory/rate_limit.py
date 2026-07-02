from __future__ import annotations

import os
import time
from collections.abc import MutableMapping


class SubmitRateLimiter:
    def __init__(
        self,
        *,
        limit: int | None = None,
        window_seconds: float | None = None,
    ) -> None:
        self.limit = (
            limit
            if limit is not None
            else int(os.environ.get("INVENTORY_SUBMIT_RATE_LIMIT", "300"))
        )
        self.window_seconds = (
            window_seconds
            if window_seconds is not None
            else (int(os.environ.get("INVENTORY_SUBMIT_RATE_WINDOW_MS", "60000")) / 1000)
        )
        self._entries: MutableMapping[str, tuple[int, float]] = {}

    def allow(self, client_id: str, cost: int = 1) -> bool:
        now = time.monotonic()
        count, reset_at = self._entries.get(client_id, (0, 0.0))
        if now >= reset_at:
            self._entries[client_id] = (cost, now + self.window_seconds)
            return True
        if count + cost > self.limit:
            return False
        self._entries[client_id] = (count + cost, reset_at)
        return True

    def prune(self) -> None:
        now = time.monotonic()
        expired = [
            client_id for client_id, (_, reset_at) in self._entries.items() if now >= reset_at
        ]
        for client_id in expired:
            del self._entries[client_id]


_submit_rate_limiter = SubmitRateLimiter()


def get_submit_rate_limiter() -> SubmitRateLimiter:
    return _submit_rate_limiter

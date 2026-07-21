from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset_at: int


class GatewayRateLimiter:
    def __init__(self, window_seconds: float = 60.0, prune_interval: int = 1_000) -> None:
        self.window_seconds = window_seconds
        self.prune_interval = prune_interval
        self._requests: dict[str, deque[float]] = {}
        self._checks = 0

    def check(self, identity: str, limit: int, now: float | None = None) -> RateLimitResult:
        current = time.monotonic() if now is None else now
        wall_now = time.time()
        if limit <= 0:
            return RateLimitResult(True, limit, 0, 0, int(wall_now))

        self._checks += 1
        if self._checks >= self.prune_interval:
            self.prune(current)
            self._checks = 0

        requests = self._requests.setdefault(identity, deque())
        cutoff = current - self.window_seconds
        while requests and requests[0] <= cutoff:
            requests.popleft()

        if len(requests) >= limit:
            retry_after = max(1, math.ceil(requests[0] + self.window_seconds - current))
            reset_at = math.ceil(wall_now + retry_after)
            return RateLimitResult(False, limit, 0, retry_after, reset_at)

        requests.append(current)
        remaining = max(0, limit - len(requests))
        reset_in = max(1, math.ceil(requests[0] + self.window_seconds - current))
        return RateLimitResult(True, limit, remaining, reset_in, math.ceil(wall_now + reset_in))

    def prune(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        for identity, requests in list(self._requests.items()):
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if not requests:
                del self._requests[identity]

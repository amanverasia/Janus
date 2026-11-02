from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class DispatchStoppedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def status(self) -> int:
        if self.reason == "client_disconnected":
            return 499
        return 504 if self.reason == "dispatch_deadline_exceeded" else 503


class DispatchBudget:
    def __init__(self, timeout_s: float, max_attempts: int) -> None:
        self.deadline = time.monotonic() + timeout_s
        self.max_attempts = max_attempts
        self.attempted = 0
        self.candidates = 0
        self.reason: str | None = None

    @property
    def skipped(self) -> int:
        return max(0, self.candidates - self.attempted)

    def stop(self, reason: str) -> DispatchStoppedError:
        self.reason = reason
        return DispatchStoppedError(reason)

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise self.stop("dispatch_deadline_exceeded")
        if self.attempted >= self.max_attempts:
            raise self.stop("dispatch_attempt_limit")

    def start_attempt(self) -> None:
        self.check()
        self.attempted += 1

    async def run(
        self,
        work: Coroutine[Any, Any, T],
        disconnected: Callable[[], Awaitable[bool]],
    ) -> T:
        async def watch_disconnect() -> None:
            while not await disconnected():
                await asyncio.sleep(0.05)

        task = asyncio.create_task(work)
        watcher = asyncio.create_task(watch_disconnect())
        try:
            done, _ = await asyncio.wait(
                {task, watcher},
                timeout=max(0, self.deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if watcher in done:
                watcher.result()
                raise self.stop("client_disconnected")
            if task in done:
                return task.result()
            raise self.stop("dispatch_deadline_exceeded")
        except asyncio.CancelledError:
            self.reason = "client_disconnected"
            raise
        finally:
            for pending in (task, watcher):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, watcher, return_exceptions=True)

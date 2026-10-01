from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from janus.storage.attempt_signals import record_attempt_signal

logger = logging.getLogger(__name__)

MIN_TPS_WINDOW_S = 0.05

_signal_tasks: set[asyncio.Task[None]] = set()


def compute_output_tps(output_tokens: int, seconds: float) -> float | None:
    if output_tokens <= 0 or seconds < MIN_TPS_WINDOW_S:
        return None
    return output_tokens / seconds


def _on_done(task: asyncio.Task[None]) -> None:
    _signal_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning("Attempt signal write failed: %s", error)


async def drain_attempt_signal_tasks() -> None:
    while _signal_tasks:
        pending = list(_signal_tasks)
        _signal_tasks.clear()
        await asyncio.gather(*pending, return_exceptions=True)


class AttemptSignal:
    def __init__(
        self,
        db_path: str | Path,
        *,
        model: str,
        account_id: str,
        provider_id: str | None,
        client_format: str | None,
        streamed: bool,
    ) -> None:
        self._db_path = db_path
        self._model = model
        self._account_id = account_id
        self._provider_id = provider_id
        self._client_format = client_format
        self._streamed = streamed
        self.started_at = time.monotonic()
        self.finished = False

    def finish(
        self,
        outcome: str,
        *,
        status: int | None,
        output_tokens: int = 0,
        first_content_at: float | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        ended = time.monotonic()
        ttft_ms = (
            int((first_content_at - self.started_at) * 1000)
            if first_content_at is not None
            else None
        )
        tps: float | None = None
        if outcome == "ok":
            if self._streamed:
                if first_content_at is not None:
                    tps = compute_output_tps(output_tokens, ended - first_content_at)
            else:
                tps = compute_output_tps(output_tokens, ended - self.started_at)
        coro = record_attempt_signal(
            self._db_path,
            model=self._model,
            account_id=self._account_id,
            provider_id=self._provider_id,
            client_format=self._client_format,
            streamed=self._streamed,
            outcome=outcome,
            status=status,
            ttft_ms=ttft_ms,
            duration_ms=int((ended - self.started_at) * 1000),
            output_tokens=output_tokens,
            output_tps=tps,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        _signal_tasks.add(task)
        task.add_done_callback(_on_done)

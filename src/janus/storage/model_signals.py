from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .database import get_connection

logger = logging.getLogger(__name__)

SUFFICIENT_SAMPLES = 5
CACHE_TTL_S = 60.0

_CacheKey = tuple[str, int, int]


@dataclass(frozen=True)
class SignalStats:
    samples: int
    errors: int
    error_rate: float | None
    ttft_p50_ms: int | None
    ttft_p90_ms: int | None
    tps_p50: float | None
    tps_p90: float | None
    nonstream_tps_p50: float | None
    last_seen: str | None
    sufficient: bool


@dataclass(frozen=True)
class ModelSignals:
    by_model: dict[str, SignalStats]
    by_account: dict[tuple[str, str], SignalStats]
    by_provider: dict[tuple[str, str], _Acc] = field(
        default_factory=dict, repr=False, compare=False
    )

    def for_providers(self, model: str, provider_ids: Collection[str]) -> SignalStats | None:
        merged: _Acc | None = None
        for provider_id in provider_ids:
            acc = self.by_provider.get((model, provider_id))
            if acc is None or acc.samples == 0:
                continue
            if merged is None:
                merged = _Acc()
            merged.merge(acc)
        return merged.stats() if merged is not None else None


_cache: dict[_CacheKey, tuple[float, ModelSignals]] = {}
_inflight: dict[_CacheKey, asyncio.Task[ModelSignals]] = {}


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = math.ceil(pct / 100 * len(ordered)) - 1
    return ordered[min(max(idx, 0), len(ordered) - 1)]


def invalidate_model_signals_cache(db_path: str | Path | None = None) -> None:
    if db_path is None:
        _cache.clear()
        _inflight.clear()
        return
    path = str(db_path)
    for key in [k for k in _cache if k[0] == path]:
        _cache.pop(key, None)
    for key in [k for k in _inflight if k[0] == path]:
        _inflight.pop(key, None)


async def _query(db_path: str | Path, window_days: int, max_rows: int) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT timestamp, model, provider_id, account_id, streamed, outcome,
                      ttft_ms, output_tps
               FROM attempt_signals
               WHERE timestamp >= datetime('now', ?)
               ORDER BY id DESC LIMIT ?""",
            (f"-{int(window_days)} days", int(max_rows)),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


class _Acc:
    def __init__(self) -> None:
        self.samples = 0
        self.errors = 0
        self.ttft: list[float] = []
        self.tps: list[float] = []
        self.nonstream_tps: list[float] = []
        self.last_seen: str | None = None

    def merge(self, other: _Acc) -> None:
        self.samples += other.samples
        self.errors += other.errors
        self.ttft.extend(other.ttft)
        self.tps.extend(other.tps)
        self.nonstream_tps.extend(other.nonstream_tps)
        if other.last_seen is not None and (
            self.last_seen is None or other.last_seen > self.last_seen
        ):
            self.last_seen = other.last_seen

    def add(self, row: dict[str, Any]) -> None:
        ts = row["timestamp"]
        if ts is not None and (self.last_seen is None or ts > self.last_seen):
            self.last_seen = str(ts)
        outcome = row["outcome"]
        if outcome not in ("ok", "error"):
            return
        self.samples += 1
        if outcome == "error":
            self.errors += 1
            return
        if row["streamed"]:
            if row["ttft_ms"] is not None:
                self.ttft.append(float(row["ttft_ms"]))
            if row["output_tps"] is not None:
                self.tps.append(float(row["output_tps"]))
        elif row["output_tps"] is not None:
            self.nonstream_tps.append(float(row["output_tps"]))

    def stats(self) -> SignalStats:
        p50 = percentile(self.ttft, 50)
        p90 = percentile(self.ttft, 90)
        return SignalStats(
            samples=self.samples,
            errors=self.errors,
            error_rate=self.errors / self.samples if self.samples else None,
            ttft_p50_ms=round(p50) if p50 is not None else None,
            ttft_p90_ms=round(p90) if p90 is not None else None,
            tps_p50=percentile(self.tps, 50),
            tps_p90=percentile(self.tps, 90),
            nonstream_tps_p50=percentile(self.nonstream_tps, 50),
            last_seen=self.last_seen,
            sufficient=self.samples >= SUFFICIENT_SAMPLES,
        )


def _aggregate(rows: list[dict[str, Any]]) -> ModelSignals:
    by_model: dict[str, _Acc] = {}
    by_account: dict[tuple[str, str], _Acc] = {}
    by_provider: dict[tuple[str, str], _Acc] = {}
    for row in rows:
        model = str(row["model"])
        by_model.setdefault(model, _Acc()).add(row)
        by_account.setdefault((model, str(row["account_id"])), _Acc()).add(row)
        if row["provider_id"] is not None:
            by_provider.setdefault((model, str(row["provider_id"])), _Acc()).add(row)
    return ModelSignals(
        by_model={k: v.stats() for k, v in by_model.items()},
        by_account={k: v.stats() for k, v in by_account.items()},
        by_provider=by_provider,
    )


async def _load(db_path: str | Path, window_days: int, max_rows: int) -> ModelSignals:
    try:
        rows = await _query(db_path, window_days, max_rows)
        return _aggregate(rows)
    except Exception as e:
        logger.warning("Failed to load model signals: %s", e)
        return ModelSignals({}, {})


async def _load_and_cache(
    key: _CacheKey, db_path: str | Path, window_days: int, max_rows: int
) -> ModelSignals:
    result = await _load(db_path, window_days, max_rows)
    current = asyncio.current_task()
    if _inflight.get(key) is current:
        _cache[key] = (time.monotonic() + CACHE_TTL_S, result)
        _inflight.pop(key, None)
    return result


async def get_model_signals(
    db_path: str | Path, *, window_days: int = 7, max_rows: int = 50_000
) -> ModelSignals:
    key: _CacheKey = (str(db_path), int(window_days), int(max_rows))
    cached = _cache.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    task = _inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(_load_and_cache(key, db_path, window_days, max_rows))
        _inflight[key] = task
    return await asyncio.shield(task)

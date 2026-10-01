from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from janus.models.catalog import _table_exists
from janus.routing.provider_snapshots import ProviderSnapshot
from janus.routing.reachability import ReachabilityReport, classify, collect_known_models
from janus.storage.providers_db import list_providers
from janus.storage.upstream_models import list_distinct_discovered_models

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 4
_reports: OrderedDict[int, tuple[ProviderSnapshot, ReachabilityReport]] = OrderedDict()


def _lock_for(app: FastAPI) -> asyncio.Lock:
    lock: asyncio.Lock | None = getattr(app.state, "_reachability_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state._reachability_lock = lock
    return lock


def _cached(snapshot: ProviderSnapshot) -> ReachabilityReport | None:
    entry = _reports.get(id(snapshot))
    if entry is not None and entry[0] is snapshot:
        return entry[1]
    return None


async def _build(db_path: Path, snapshot: ProviderSnapshot) -> ReachabilityReport:
    provider_rows = await list_providers(db_path)
    discovered: list[dict[str, Any]] = []
    if await _table_exists(db_path, "upstream_models"):
        discovered = await list_distinct_discovered_models(db_path)
    known = collect_known_models(provider_rows, discovered)
    return classify(known, snapshot.registry, provider_rows)


async def get_reachability_report(app: FastAPI, snapshot: ProviderSnapshot) -> ReachabilityReport:
    report = _cached(snapshot)
    if report is not None:
        return report
    async with _lock_for(app):
        report = _cached(snapshot)
        if report is not None:
            return report
        try:
            report = await _build(Path(app.state.db_path), snapshot)
        except Exception:
            logger.warning("Failed to build model reachability report", exc_info=True)
            return ReachabilityReport([], [])
        _reports[id(snapshot)] = (snapshot, report)
        while len(_reports) > _MAX_ENTRIES:
            _reports.popitem(last=False)
        return report

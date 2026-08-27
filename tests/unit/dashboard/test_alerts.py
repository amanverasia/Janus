from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from janus.config.schema import JanusConfig, ProviderConfig, ServerSettings
from janus.dashboard.alerts import (
    DashboardAlert,
    collect_dashboard_alerts,
    collect_dashboard_alerts_cached,
    invalidate_dashboard_alerts,
)
from janus.storage.budgets import create_or_update_budget
from janus.storage.database import get_connection, init_db, seed_from_config


@pytest.fixture
async def db(tmp_path: Path) -> Path:
    cfg = JanusConfig(
        server=ServerSettings(port=0, data_dir=tmp_path),
        providers=[
            ProviderConfig(
                id="t",
                prefix="t",
                api_type="openai_compat",
                base_url="https://test.local/v1",
                api_key="k",
                models=["m1"],
            )
        ],
    )
    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    await seed_from_config(db_path, cfg)
    return db_path


def _fake_request() -> MagicMock:
    req = MagicMock()
    req.app.state.pricing_registry = MagicMock()
    req.app.state.pricing_registry.get.return_value = None
    return req


@pytest.mark.asyncio
async def test_budget_warning_alert(db: Path) -> None:
    await create_or_update_budget(db, key_id=None, daily_limit=10.0, warn_pct=80)
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO usage (cost, input_tokens, output_tokens, status) "
            "VALUES (9.0, 100, 50, 200)"
        )
        await conn.commit()
    result = await collect_dashboard_alerts(db, _fake_request())
    ids = [a.id for a in result["alerts"]]
    assert "budget:global" in ids
    assert result["summary"] in ("warning", "critical")


@pytest.mark.asyncio
async def test_no_providers_critical(db: Path) -> None:
    async with get_connection(db) as conn:
        await conn.execute("UPDATE providers SET is_enabled = 0")
        await conn.commit()
    result = await collect_dashboard_alerts(db, _fake_request())
    assert any(a.id == "setup:no_providers" for a in result["alerts"])
    assert result["summary"] == "critical"


@pytest.mark.asyncio
async def test_alert_cache_coalesces_requests_and_supports_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "unused.db"
    calls = 0
    release = asyncio.Event()
    result = {"alerts": [], "summary": "ok", "counts": {"critical": 0, "warning": 0}}

    async def fake_collect(_db_path: Path, _request: MagicMock) -> tuple[dict[str, Any], bool]:
        nonlocal calls
        calls += 1
        await release.wait()
        return result, True

    monkeypatch.setattr(
        "janus.dashboard.alerts._collect_dashboard_alerts_with_status", fake_collect
    )
    app = FastAPI()
    request = _fake_request()
    request.app = app
    first = asyncio.create_task(collect_dashboard_alerts_cached(db_path, request))
    second = asyncio.create_task(collect_dashboard_alerts_cached(db_path, request))
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(first, second) == [result, result]
    assert calls == 1

    assert await collect_dashboard_alerts_cached(db_path, request) == result
    assert calls == 1
    invalidate_dashboard_alerts(app)
    assert await collect_dashboard_alerts_cached(db_path, request) == result
    assert calls == 2

    invalidate_dashboard_alerts(app)
    assert await collect_dashboard_alerts_cached(db_path, request, max_age_seconds=0) == result
    assert await collect_dashboard_alerts_cached(db_path, request) == result
    assert calls == 4


@pytest.mark.asyncio
async def test_alert_cache_keeps_last_good_value_after_partial_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = {"alerts": [], "summary": "ok", "counts": {"critical": 0, "warning": 0}}
    partial = {
        "alerts": [],
        "summary": "warning",
        "counts": {"critical": 0, "warning": 1},
    }
    recovered = {
        "alerts": [],
        "summary": "critical",
        "counts": {"critical": 1, "warning": 0},
    }
    outcomes = iter(((good, True), (partial, False), (recovered, True)))

    async def fake_collect(_db_path: Path, _request: MagicMock) -> tuple[dict[str, Any], bool]:
        return next(outcomes)

    monkeypatch.setattr(
        "janus.dashboard.alerts._collect_dashboard_alerts_with_status", fake_collect
    )
    app = FastAPI()
    request = _fake_request()
    request.app = app
    db_path = tmp_path / "unused.db"

    assert await collect_dashboard_alerts_cached(db_path, request, max_age_seconds=0) == good
    assert await collect_dashboard_alerts_cached(db_path, request) == good
    assert await collect_dashboard_alerts_cached(db_path, request) == recovered


@pytest.mark.asyncio
async def test_invalidation_during_alert_refresh_does_not_publish_stale_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = {"alerts": [], "summary": "ok", "counts": {"critical": 0, "warning": 0}}
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_collect(_db_path: Path, _request: MagicMock) -> tuple[dict[str, Any], bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return result, True

    monkeypatch.setattr(
        "janus.dashboard.alerts._collect_dashboard_alerts_with_status", fake_collect
    )
    app = FastAPI()
    request = _fake_request()
    request.app = app
    db_path = tmp_path / "unused.db"

    refresh = asyncio.create_task(collect_dashboard_alerts_cached(db_path, request))
    await started.wait()
    invalidate_dashboard_alerts(app)
    release.set()
    assert await refresh == result
    assert app.state._dashboard_alert_cache is None
    assert await collect_dashboard_alerts_cached(db_path, request) == result
    assert calls == 2


@pytest.mark.asyncio
async def test_partial_alert_refresh_cannot_return_cache_invalidated_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = {
        "alerts": [],
        "summary": "warning",
        "counts": {"critical": 0, "warning": 1},
    }
    partial = {"alerts": [], "summary": "ok", "counts": {"critical": 0, "warning": 0}}
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_collect(_db_path: Path, _request: MagicMock) -> tuple[dict[str, Any], bool]:
        started.set()
        await release.wait()
        return partial, False

    monkeypatch.setattr(
        "janus.dashboard.alerts._collect_dashboard_alerts_with_status", fake_collect
    )
    app = FastAPI()
    app.state._dashboard_alert_cache = (0.0, 0, stale)
    request = _fake_request()
    request.app = app

    refresh = asyncio.create_task(collect_dashboard_alerts_cached(tmp_path / "unused.db", request))
    await started.wait()
    invalidate_dashboard_alerts(app)
    release.set()

    assert await refresh == partial
    assert app.state._dashboard_alert_cache is None


@pytest.mark.asyncio
async def test_alert_collection_isolates_collector_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warning = DashboardAlert(
        id="quota:test",
        severity="warning",
        title="Quota warning",
        detail="Quota is nearly exhausted.",
        href="/dashboard/ui/providers",
    )

    async def fail(_db_path: Path, _request: MagicMock) -> list[DashboardAlert]:
        raise RuntimeError("temporary failure")

    async def succeed(_db_path: Path, _request: MagicMock) -> list[DashboardAlert]:
        return [warning]

    async def empty(_db_path: Path, _request: MagicMock) -> list[DashboardAlert]:
        return []

    monkeypatch.setattr("janus.dashboard.alerts._budget_alerts", fail)
    monkeypatch.setattr("janus.dashboard.alerts._quota_alerts", succeed)
    for name in ("_cooldown_alerts", "_inventory_alerts", "_unpriced_alerts", "_setup_alerts"):
        monkeypatch.setattr(f"janus.dashboard.alerts.{name}", empty)

    result = await collect_dashboard_alerts(tmp_path / "unused.db", _fake_request())

    assert result["alerts"] == [warning]
    assert result["summary"] == "warning"

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from janus.dashboard.inventory_routes import _run_all_keys
from janus.inventory import scheduler


async def test_recheck_all_logs_list_failure(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.ERROR, logger="janus.dashboard.inventory_routes"),
        patch(
            "janus.dashboard.inventory_routes.list_upstream_keys",
            AsyncMock(side_effect=RuntimeError("decrypt failed")),
        ),
    ):
        await _run_all_keys(tmp_path / "janus.db")

    assert "Inventory recheck-all task failed" in caplog.text
    assert "decrypt failed" in caplog.text


async def test_scheduler_logs_failure_and_keeps_running(
    tmp_path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop_event = asyncio.Event()
    calls = 0

    async def check_all(_db_path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("decrypt failed")
        stop_event.set()

    check = AsyncMock(side_effect=check_all)
    monkeypatch.setattr(scheduler, "CHECK_INTERVAL_HOURS", 0)

    with (
        caplog.at_level(logging.ERROR, logger="janus.inventory.scheduler"),
        patch("janus.inventory.key_checker.check_all_upstream_keys", check),
    ):
        await scheduler.run_inventory_scheduler(tmp_path / "janus.db", stop_event)

    assert check.await_count == 2
    assert "Scheduled inventory key check failed" in caplog.text
    assert "decrypt failed" in caplog.text

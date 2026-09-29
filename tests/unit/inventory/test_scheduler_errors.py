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
    monkeypatch.setattr(scheduler, "_interval_seconds", lambda: 0.01)

    with (
        caplog.at_level(logging.ERROR, logger="janus.inventory.scheduler"),
        patch("janus.inventory.key_checker.check_all_upstream_keys", check),
    ):
        await scheduler.run_inventory_scheduler(tmp_path / "janus.db", stop_event)

    assert check.await_count == 2
    assert "Scheduled inventory key check failed" in caplog.text
    assert "decrypt failed" in caplog.text


def test_scheduler_interval_clamped_to_one_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "CHECK_INTERVAL_HOURS", 0)
    assert scheduler._interval_seconds() == 3600.0
    monkeypatch.setattr(scheduler, "CHECK_INTERVAL_HOURS", 12)
    assert scheduler._interval_seconds() == 43200.0


async def test_scheduler_checks_at_startup(tmp_path) -> None:
    stop_event = asyncio.Event()
    check = AsyncMock(side_effect=lambda _db_path: stop_event.set())

    with patch("janus.inventory.key_checker.check_all_upstream_keys", check):
        await scheduler.run_inventory_scheduler(tmp_path / "janus.db", stop_event)

    assert check.await_count == 1


async def test_recheck_all_task_is_tracked_until_done(tmp_path) -> None:
    from janus import background
    from janus.dashboard import inventory_routes

    release = asyncio.Event()

    async def fake_run_all(_db_path) -> None:
        await release.wait()

    with patch.object(inventory_routes, "_run_all_keys", fake_run_all):
        task = inventory_routes._schedule_recheck_all(tmp_path / "janus.db")
        assert task in background.background_tasks()
        release.set()
        await task

    assert task not in background.background_tasks()


async def test_recheck_all_preserves_manual_review_state(tmp_path) -> None:
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import (
        create_upstream_key,
        get_upstream_key,
        update_upstream_key,
    )

    db_path = tmp_path / "janus.db"
    await init_db(db_path)
    unidentified = await create_upstream_key(
        db_path, provider_id="unidentified", key_value="mystery-credential-value-1"
    )
    await update_upstream_key(
        db_path,
        unidentified["id"],
        {"status": "invalid", "last_error": "Provider could not be identified"},
    )
    paused = await create_upstream_key(
        db_path, provider_id="openai", key_value="sk-proj-paused-credential-value"
    )
    await update_upstream_key(
        db_path,
        paused["id"],
        {
            "status": "validation_paused",
            "last_error": "Paused after repeated failures",
            "consecutive_failures": 5,
            "validation_paused_at": "2026-09-28 00:00:00",
        },
    )
    eligible = await create_upstream_key(
        db_path, provider_id="openai", key_value="sk-proj-eligible-credential-value"
    )
    await update_upstream_key(db_path, eligible["id"], {"status": "invalid", "last_error": "stale"})

    with patch(
        "janus.dashboard.inventory_routes.check_all_upstream_keys", AsyncMock(return_value=1)
    ):
        await _run_all_keys(db_path)

    kept_unidentified = await get_upstream_key(db_path, unidentified["id"])
    assert kept_unidentified is not None
    assert kept_unidentified["status"] == "invalid"
    assert kept_unidentified["last_error"] == "Provider could not be identified"

    kept_paused = await get_upstream_key(db_path, paused["id"])
    assert kept_paused is not None
    assert kept_paused["status"] == "validation_paused"
    assert kept_paused["last_error"] == "Paused after repeated failures"
    assert kept_paused["consecutive_failures"] == 5
    assert kept_paused["validation_paused_at"] == "2026-09-28 00:00:00"

    reset = await get_upstream_key(db_path, eligible["id"])
    assert reset is not None
    assert reset["status"] == "pending_validation"
    assert reset["last_error"] is None

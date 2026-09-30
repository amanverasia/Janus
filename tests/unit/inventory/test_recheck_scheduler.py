from unittest.mock import AsyncMock

import pytest

from janus import background
from janus.inventory import recheck_scheduler


@pytest.mark.asyncio
async def test_manual_recheck_resets_pause_before_checking(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    key_id = "paused-key"
    calls = []

    async def fake_update(db_path_arg, key_id_arg, fields):
        calls.append(("update", db_path_arg, key_id_arg, fields))

    async def fake_check(db_path_arg, key_id_arg):
        calls.append(("check", db_path_arg, key_id_arg))

    monkeypatch.setattr(
        recheck_scheduler, "update_upstream_key", AsyncMock(side_effect=fake_update)
    )
    monkeypatch.setattr(recheck_scheduler, "check_upstream_key", AsyncMock(side_effect=fake_check))
    scheduled_task = recheck_scheduler.schedule_upstream_recheck(key_id, db_path)
    assert scheduled_task in background.background_tasks()
    await scheduled_task

    assert calls == [
        (
            "update",
            db_path,
            key_id,
            {
                "status": "pending_validation",
                "last_error": None,
                "consecutive_failures": 0,
                "validation_paused_at": None,
            },
        ),
        ("check", db_path, key_id),
    ]


async def test_manual_and_bulk_checks_share_probe_concurrency(tmp_path, monkeypatch):
    import asyncio

    from janus.inventory import key_checker
    from janus.storage import upstream_keys

    monkeypatch.setattr(key_checker, "CHECK_CONCURRENCY", 2)
    active = 0
    peak = 0
    checked = set()

    async def probe(db_path, key_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            checked.add(key_id)
        finally:
            active -= 1

    monkeypatch.setattr(key_checker, "_check_upstream_key", probe)
    monkeypatch.setattr(recheck_scheduler, "update_upstream_key", AsyncMock())
    monkeypatch.setattr(
        upstream_keys,
        "list_upstream_keys",
        AsyncMock(return_value=[{"id": f"bulk-{i}", "provider_id": "openai"} for i in range(6)]),
    )
    tasks = [recheck_scheduler.schedule_upstream_recheck(f"manual-{i}", tmp_path) for i in range(6)]
    await asyncio.gather(*tasks, key_checker.check_all_upstream_keys(tmp_path))
    assert peak == 2
    assert len(checked) == 12


async def test_cancelled_probe_releases_shared_slot(tmp_path, monkeypatch):
    import asyncio

    from janus.inventory import key_checker

    monkeypatch.setattr(key_checker, "CHECK_CONCURRENCY", 1)
    entered = asyncio.Event()

    async def probe(db_path, key_id):
        if key_id == "cancel":
            entered.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(key_checker, "_check_upstream_key", probe)
    task = asyncio.create_task(key_checker.check_upstream_key(tmp_path, "cancel"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(key_checker.check_upstream_key(tmp_path, "next"), timeout=1)

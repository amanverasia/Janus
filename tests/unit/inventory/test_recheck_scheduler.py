import asyncio
from unittest.mock import AsyncMock

import pytest

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
    real_create_task = asyncio.create_task
    scheduled_task = None

    def capture_task(coro):
        nonlocal scheduled_task
        scheduled_task = real_create_task(coro)
        return scheduled_task

    monkeypatch.setattr(recheck_scheduler.asyncio, "create_task", capture_task)

    recheck_scheduler.schedule_upstream_recheck(key_id, db_path)
    assert scheduled_task is not None
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

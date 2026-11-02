import asyncio
from unittest.mock import AsyncMock

import pytest

from janus.routing.dispatch import DispatchBudget, DispatchStoppedError


async def test_attempt_limit_bounds_hundreds_of_failures():
    budget = DispatchBudget(60, 20)
    budget.candidates = 500

    async def fail_pool():
        for _ in range(500):
            budget.start_attempt()

    with pytest.raises(DispatchStoppedError) as error:
        await budget.run(fail_pool(), AsyncMock(return_value=False))
    assert error.value.reason == "dispatch_attempt_limit"
    assert budget.attempted == 20
    assert budget.skipped == 480


@pytest.mark.parametrize("disconnected", [False, True])
async def test_deadline_or_disconnect_cancels_and_drains_upstream(disconnected):
    budget = DispatchBudget(0.03, 20)
    closed = asyncio.Event()

    async def pending_upstream():
        budget.start_attempt()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    with pytest.raises(DispatchStoppedError) as error:
        await budget.run(pending_upstream(), AsyncMock(return_value=disconnected))
    assert error.value.reason == (
        "client_disconnected" if disconnected else "dispatch_deadline_exceeded"
    )
    assert closed.is_set()
    assert budget.attempted == 1


async def test_external_cancellation_drains_children():
    budget = DispatchBudget(60, 20)
    started = asyncio.Event()
    closed = asyncio.Event()

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    task = asyncio.create_task(budget.run(pending(), AsyncMock(return_value=False)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert budget.reason == "client_disconnected"


async def test_expired_budget_starts_no_attempt(monkeypatch):
    budget = DispatchBudget(60, 20)
    monkeypatch.setattr("janus.routing.dispatch.time.monotonic", lambda: budget.deadline)
    with pytest.raises(DispatchStoppedError) as error:
        budget.start_attempt()
    assert error.value.status == 504
    assert budget.attempted == 0


async def test_fusion_limit_cancels_other_panel_work():
    from janus.routing.fusion import collect_panel

    budget = DispatchBudget(60, 2)
    closed = []

    async def panel(index):
        budget.start_attempt()
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(index)

    tasks = [asyncio.create_task(panel(i)) for i in range(20)]
    with pytest.raises(DispatchStoppedError):
        await collect_panel(tasks, min_panel=2, straggler_grace_s=1, hard_timeout_s=60)
    assert budget.attempted == 2
    assert sorted(closed) == [0, 1]
    assert all(task.done() for task in tasks)

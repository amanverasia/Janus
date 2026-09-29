import asyncio
import gc
import logging

from janus import background


async def test_spawned_task_is_strongly_referenced_until_done():
    release = asyncio.Event()
    finished = []

    async def work() -> None:
        await release.wait()
        finished.append(True)

    task = background.spawn_background(work(), name="test-strong-ref")
    task_id = id(task)
    del task
    gc.collect()

    assert any(id(t) == task_id for t in background.background_tasks())
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert finished == [True]
    assert all(id(t) != task_id for t in background.background_tasks())


async def test_spawned_task_exception_is_logged(caplog):
    async def boom() -> None:
        raise RuntimeError("probe exploded")

    with caplog.at_level(logging.ERROR, logger="janus.background"):
        task = background.spawn_background(boom(), name="test-boom")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert task not in background.background_tasks()
    records = [r for r in caplog.records if "test-boom" in r.getMessage()]
    assert records
    assert records[0].exc_info is not None
    assert "probe exploded" in str(records[0].exc_info[1])


async def test_cancelled_task_is_not_logged_as_failure(caplog):
    async def forever() -> None:
        await asyncio.Event().wait()

    with caplog.at_level(logging.ERROR, logger="janus.background"):
        task = background.spawn_background(forever(), name="test-cancel")
        await asyncio.sleep(0)
        await background.cancel_background_tasks()

    assert task.cancelled()
    assert not background.background_tasks()
    assert not [r for r in caplog.records if "test-cancel" in r.getMessage()]


async def test_cancel_background_tasks_with_nothing_pending():
    await background.cancel_background_tasks()
    assert not background.background_tasks()

import asyncio
import types

import pytest

from janus.api import signals
from janus.storage.attempt_signals import list_attempt_signals, reset_attempt_signal_prune_throttle
from janus.storage.database import init_db


@pytest.fixture(autouse=True)
def _reset():
    reset_attempt_signal_prune_throttle()


def test_compute_output_tps():
    assert signals.compute_output_tps(0, 1.0) is None
    assert signals.compute_output_tps(10, 0.01) is None
    assert signals.compute_output_tps(100, 2.0) == pytest.approx(50.0)


def _sig(db, streamed=True):
    return signals.AttemptSignal(
        db,
        model="m1",
        account_id="acct-a",
        provider_id="acct-a",
        client_format="openai",
        streamed=streamed,
    )


async def test_stream_ok_records_ttft_and_tps(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    times = iter([100.0, 103.0])
    monkeypatch.setattr(signals, "time", types.SimpleNamespace(monotonic=lambda: next(times)))
    s = _sig(db)
    s.finish("ok", status=200, output_tokens=100, first_content_at=101.0)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["outcome"] == "ok"
    assert r["ttft_ms"] == 1000
    assert r["duration_ms"] == 3000
    assert r["output_tps"] == pytest.approx(50.0)
    assert r["streamed"] == 1


async def test_nonstream_ok_tps_uses_full_duration(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    times = iter([0.0, 2.0])
    monkeypatch.setattr(signals, "time", types.SimpleNamespace(monotonic=lambda: next(times)))
    s = _sig(db, streamed=False)
    s.finish("ok", status=200, output_tokens=40)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["ttft_ms"] is None
    assert r["output_tps"] == pytest.approx(20.0)
    assert r["streamed"] == 0


async def test_error_has_no_tps(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    _sig(db).finish("error", status=500, output_tokens=5, first_content_at=None)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["outcome"] == "error"
    assert r["status"] == 500
    assert r["output_tps"] is None


async def test_aborted_keeps_ttft_drops_tps(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    times = iter([0.0, 5.0])
    monkeypatch.setattr(signals, "time", types.SimpleNamespace(monotonic=lambda: next(times)))
    _sig(db).finish("aborted", status=499, output_tokens=50, first_content_at=0.5)
    await signals.drain_attempt_signal_tasks()
    r = (await list_attempt_signals(db))[0]
    assert r["ttft_ms"] == 500
    assert r["output_tps"] is None


async def test_finish_is_idempotent(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    s = _sig(db)
    s.finish("error", status=500)
    s.finish("ok", status=200)
    await signals.drain_attempt_signal_tasks()
    rows = await list_attempt_signals(db)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert s.finished is True


async def test_unfinished_signal_writes_nothing(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    _sig(db)
    await signals.drain_attempt_signal_tasks()
    assert await list_attempt_signals(db) == []


def test_finish_without_loop_does_not_raise(tmp_path):
    s = _sig(tmp_path / "j.db")
    s.finish("ok", status=200)
    assert s.finished is True


async def test_drain_survives_failing_task(monkeypatch, tmp_path):
    async def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(signals, "record_attempt_signal", boom)
    _sig(tmp_path / "j.db").finish("ok", status=200)
    await signals.drain_attempt_signal_tasks()
    await asyncio.sleep(0)

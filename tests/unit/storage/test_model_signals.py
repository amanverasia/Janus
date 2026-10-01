import asyncio

import pytest

from janus.storage import model_signals as ms
from janus.storage.attempt_signals import record_attempt_signal, reset_attempt_signal_prune_throttle
from janus.storage.database import get_connection, init_db


@pytest.fixture(autouse=True)
def _reset():
    reset_attempt_signal_prune_throttle()
    ms.invalidate_model_signals_cache()
    yield
    ms.invalidate_model_signals_cache()


async def _add(db, *, model="m1", account="a", outcome="ok", streamed=True, ttft=100, tps=50.0):
    await record_attempt_signal(
        db,
        model=model,
        account_id=account,
        provider_id=account,
        client_format="openai",
        streamed=streamed,
        outcome=outcome,
        status=200 if outcome == "ok" else 500,
        ttft_ms=ttft,
        duration_ms=1000,
        output_tokens=10,
        output_tps=tps,
    )


def test_percentile_nearest_rank():
    assert ms.percentile([], 50) is None
    assert ms.percentile([5.0], 90) == 5.0
    vals = [float(v) for v in range(1, 11)]
    assert ms.percentile(vals, 50) == 5.0
    assert ms.percentile(vals, 90) == 9.0


async def test_aggregates_by_model_and_account(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    for ttft in (100, 200, 300, 400):
        await _add(db, account="a", ttft=ttft, tps=float(ttft))
    await _add(db, account="b", outcome="error", ttft=None, tps=None)
    await _add(db, account="b", outcome="client_error", ttft=None, tps=None)
    await _add(db, account="b", outcome="aborted", ttft=50, tps=None)
    out = await ms.get_model_signals(db)
    m = out.by_model["m1"]
    assert m.samples == 5
    assert m.errors == 1
    assert m.error_rate == pytest.approx(0.2)
    assert m.ttft_p50_ms == 200
    assert m.ttft_p90_ms == 400
    assert m.sufficient is True
    b = out.by_account[("m1", "b")]
    assert b.samples == 1 and b.errors == 1 and b.error_rate == 1.0
    assert b.ttft_p50_ms is None
    assert b.sufficient is False
    assert b.last_seen is not None


async def test_stream_and_nonstream_tps_split(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db, streamed=True, tps=80.0)
    await _add(db, streamed=False, ttft=None, tps=20.0)
    m = (await ms.get_model_signals(db)).by_model["m1"]
    assert m.tps_p50 == 80.0
    assert m.nonstream_tps_p50 == 20.0


async def test_window_excludes_old_rows(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db)
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO attempt_signals (timestamp, model, account_id, outcome, duration_ms)"
            " VALUES (datetime('now', '-3 days'), 'old', 'a', 'ok', 10)"
        )
        await conn.commit()
    out = await ms.get_model_signals(db, window_days=1)
    assert "old" not in out.by_model
    assert "m1" in out.by_model


async def test_max_rows_bounds_scan(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    for _ in range(5):
        await _add(db)
    out = await ms.get_model_signals(db, max_rows=3)
    assert out.by_model["m1"].samples == 3


async def test_cache_and_invalidation(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db)
    first = await ms.get_model_signals(db)
    await _add(db)
    assert (await ms.get_model_signals(db)).by_model["m1"].samples == first.by_model["m1"].samples
    ms.invalidate_model_signals_cache(db)
    assert (await ms.get_model_signals(db)).by_model["m1"].samples == 2


async def test_concurrent_misses_share_one_query(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    calls = 0
    real = ms._query

    async def counting(*a, **k):
        nonlocal calls
        calls += 1
        return await real(*a, **k)

    monkeypatch.setattr(ms, "_query", counting)
    await asyncio.gather(*(ms.get_model_signals(db) for _ in range(5)))
    assert calls == 1


async def test_db_error_returns_empty(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ms, "_query", boom)
    out = await ms.get_model_signals(tmp_path / "j.db")
    assert out.by_model == {} and out.by_account == {}


async def test_reload_providers_invalidates_cache(tmp_path, monkeypatch):
    from janus.dashboard import reload as reload_mod

    seen = []
    monkeypatch.setattr(reload_mod, "invalidate_model_signals_cache", lambda p=None: seen.append(p))
    from janus.app import create_app
    from janus.config.schema import JanusConfig, ServerSettings
    from janus.storage.database import init_db as _init

    app = create_app(
        config=JanusConfig(server=ServerSettings(port=0, require_api_key=False, data_dir=tmp_path))
    )
    await _init(app.state.db_path)
    await reload_mod.reload_providers(app)
    assert seen == [app.state.db_path]


async def test_cancelled_caller_does_not_break_shared_query(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db)
    gate = asyncio.Event()
    real = ms._query

    async def slow(*a, **k):
        await gate.wait()
        return await real(*a, **k)

    monkeypatch.setattr(ms, "_query", slow)
    first = asyncio.ensure_future(ms.get_model_signals(db))
    second = asyncio.ensure_future(ms.get_model_signals(db))
    await asyncio.sleep(0)
    first.cancel()
    gate.set()
    out = await second
    assert out.by_model["m1"].samples == 1


async def test_invalidate_during_inflight_does_not_cache_stale(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    await _add(db)
    gate = asyncio.Event()
    real = ms._query

    async def slow(*a, **k):
        await gate.wait()
        return await real(*a, **k)

    monkeypatch.setattr(ms, "_query", slow)
    pending = asyncio.ensure_future(ms.get_model_signals(db))
    await asyncio.sleep(0)
    ms.invalidate_model_signals_cache(db)
    gate.set()
    await pending
    monkeypatch.setattr(ms, "_query", real)
    await _add(db)
    assert (await ms.get_model_signals(db)).by_model["m1"].samples == 2

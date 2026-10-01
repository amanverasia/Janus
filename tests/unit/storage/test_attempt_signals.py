import pytest

from janus.storage import attempt_signals as sig
from janus.storage.database import get_connection, init_db


def _row(**overrides):
    base = dict(
        model="m1",
        account_id="acct-a",
        provider_id="acct-a",
        client_format="openai",
        streamed=True,
        outcome="ok",
        status=200,
        ttft_ms=120,
        duration_ms=900,
        output_tokens=40,
        output_tps=51.3,
    )
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_throttle():
    sig.reset_attempt_signal_prune_throttle()
    yield
    sig.reset_attempt_signal_prune_throttle()


async def test_record_and_list_roundtrip(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await sig.record_attempt_signal(db, **_row())
    rows = await sig.list_attempt_signals(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "m1"
    assert r["account_id"] == "acct-a"
    assert r["streamed"] == 1
    assert r["outcome"] == "ok"
    assert r["ttft_ms"] == 120
    assert r["output_tps"] == pytest.approx(51.3)
    assert r["timestamp"]


async def test_nullable_fields(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await sig.record_attempt_signal(
        db, **_row(status=None, ttft_ms=None, output_tps=None, streamed=False, outcome="error")
    )
    r = (await sig.list_attempt_signals(db))[0]
    assert r["status"] is None
    assert r["ttft_ms"] is None
    assert r["output_tps"] is None
    assert r["streamed"] == 0


async def test_record_swallows_errors(tmp_path):
    await sig.record_attempt_signal(tmp_path / "missing-dir" / "x" / "j.db", **_row())


async def test_prune_deletes_old_rows(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await sig.record_attempt_signal(db, **_row())
    async with get_connection(db) as conn:
        await conn.execute(
            "INSERT INTO attempt_signals (timestamp, model, account_id, outcome, duration_ms)"
            " VALUES (datetime('now', '-8 days'), 'old', 'acct-a', 'ok', 10)"
        )
        await conn.commit()
    deleted = await sig.prune_attempt_signals(db, 7)
    assert deleted == 1
    models = [r["model"] for r in await sig.list_attempt_signals(db)]
    assert models == ["m1"]


async def test_record_prunes_at_most_hourly(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    await init_db(db)
    calls = []

    async def fake_prune(path, retention_days=7):
        calls.append(path)
        return 0

    monkeypatch.setattr(sig, "prune_attempt_signals", fake_prune)
    await sig.record_attempt_signal(db, **_row())
    await sig.record_attempt_signal(db, **_row())
    assert len(calls) == 1


async def test_init_db_is_idempotent_with_table(tmp_path):
    db = tmp_path / "j.db"
    await init_db(db)
    await init_db(db)
    await sig.record_attempt_signal(db, **_row())
    assert len(await sig.list_attempt_signals(db)) == 1

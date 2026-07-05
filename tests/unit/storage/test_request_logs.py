import pytest

from janus.storage.database import init_db
from janus.storage.request_logs import (
    MAX_BODY_CHARS,
    clear_request_logs,
    count_request_logs,
    export_request_logs,
    get_request_log,
    list_request_logs,
    record_request_log,
)


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_record_and_list(db):
    await record_request_log(
        db,
        client_format="openai",
        model="test/m1",
        provider_id="test",
        account_id="test",
        status=200,
        duration_ms=42,
        request_body='{"model": "test/m1"}',
        response_body='{"ok": true}',
    )
    logs = await list_request_logs(db)
    assert len(logs) == 1
    assert logs[0]["model"] == "test/m1"
    assert logs[0]["status"] == 200
    assert logs[0]["duration_ms"] == 42
    assert "request_body" not in logs[0]


async def test_get_detail_includes_bodies(db):
    await record_request_log(db, model="m", request_body="req", response_body="resp")
    logs = await list_request_logs(db)
    detail = await get_request_log(db, logs[0]["id"])
    assert detail is not None
    assert detail["request_body"] == "req"
    assert detail["response_body"] == "resp"
    assert await get_request_log(db, 99999) is None


async def test_bodies_truncated(db):
    await record_request_log(db, request_body="x" * (MAX_BODY_CHARS + 100))
    logs = await list_request_logs(db)
    detail = await get_request_log(db, logs[0]["id"])
    assert detail is not None
    assert len(detail["request_body"]) < MAX_BODY_CHARS + 100
    assert detail["request_body"].endswith("…[truncated]")


async def test_prune_keeps_most_recent(db, monkeypatch):
    monkeypatch.setattr("janus.storage.request_logs.MAX_ROWS", 5)
    for i in range(8):
        await record_request_log(db, model=f"m{i}")
    assert await count_request_logs(db) == 5
    logs = await list_request_logs(db)
    assert logs[0]["model"] == "m7"
    assert logs[-1]["model"] == "m3"


async def test_streamed_and_error_fields(db):
    await record_request_log(db, streamed=True, status=200)
    await record_request_log(db, status=503, error="All providers exhausted")
    logs = await list_request_logs(db)
    assert logs[0]["error"] == "All providers exhausted"
    assert logs[1]["streamed"] == 1


async def test_export_and_clear(db):
    await record_request_log(db, model="m1", request_body="req")
    exported = await export_request_logs(db)
    assert len(exported) == 1
    assert exported[0]["request_body"] == "req"
    await clear_request_logs(db)
    assert await count_request_logs(db) == 0


async def test_record_is_fail_safe(tmp_path):
    missing = tmp_path / "nonexistent" / "db.sqlite"
    await record_request_log(missing, model="m")

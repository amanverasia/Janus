import pytest

from janus.storage.database import init_db
from janus.storage.outcomes import list_request_outcomes, record_request_outcome


@pytest.mark.asyncio
async def test_record_request_outcome_defaults(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_request_outcome(db_path, model="gpt-4o", status=200)
    rows = await list_request_outcomes(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "gpt-4o"
    assert row["status"] == 200
    assert row["attempts"] == 1
    assert row["streamed"] == 0
    assert row["client_format"] is None
    assert row["client_key_id"] is None


@pytest.mark.asyncio
async def test_record_request_outcome_full_fields(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_request_outcome(
        db_path,
        client_format="openai",
        model="gpt-4o",
        provider_id="openai",
        account_id="openai-0",
        status=503,
        duration_ms=1234,
        streamed=True,
        attempts=3,
        client_key_id=7,
        client_key_label="ops",
    )
    rows = await list_request_outcomes(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["client_format"] == "openai"
    assert row["provider_id"] == "openai"
    assert row["account_id"] == "openai-0"
    assert row["status"] == 503
    assert row["duration_ms"] == 1234
    assert row["streamed"] == 1
    assert row["attempts"] == 3
    assert row["client_key_id"] == 7
    assert row["client_key_label"] == "ops"


@pytest.mark.asyncio
async def test_record_request_outcome_clamps_zero_attempts(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_request_outcome(db_path, model="gpt-4o", status=429, attempts=0)
    rows = await list_request_outcomes(db_path)
    assert rows[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_record_request_outcome_never_raises_on_bad_path(tmp_path):
    await record_request_outcome("/nonexistent-dir-xyz/test.db", status=200)


@pytest.mark.asyncio
async def test_list_request_outcomes_newest_first(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_request_outcome(db_path, model="first", status=200)
    await record_request_outcome(db_path, model="second", status=500)
    rows = await list_request_outcomes(db_path)
    assert [r["model"] for r in rows] == ["second", "first"]


@pytest.mark.asyncio
async def test_init_db_backfills_outcomes_from_usage_once(tmp_path):
    import aiosqlite

    from tests.fixtures.usage_seed import seed_usage

    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"model": "gpt-4o", "status": 200},
            {"model": "claude", "status": 200},
        ],
    )
    # Simulate an upgrade: rows were recorded before request_outcomes existed.
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("DELETE FROM request_outcomes")
        await db.execute("PRAGMA user_version = 0")
        await db.commit()

    await init_db(db_path)
    rows = await list_request_outcomes(db_path)
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"gpt-4o", "claude"}
    assert all(r["status"] == 200 for r in rows)

    # Second init must not duplicate the backfill.
    await init_db(db_path)
    rows = await list_request_outcomes(db_path)
    assert len(rows) == 2

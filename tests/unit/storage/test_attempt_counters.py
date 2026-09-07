from janus.storage.attempt_counters import (
    DAILY_SCOPE,
    QUOTA_REQUESTS_SCOPE,
    bump_attempt_counter,
    get_attempt_counts,
    prune_attempt_counters,
)
from janus.storage.database import get_connection, init_db


async def test_bump_and_get_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await bump_attempt_counter(db, DAILY_SCOPE, "acct-1", "2026-09-07", 1)
    await bump_attempt_counter(db, DAILY_SCOPE, "acct-1", "2026-09-07", 2)
    await bump_attempt_counter(db, DAILY_SCOPE, "acct-2", "2026-09-07", 1)
    counts = await get_attempt_counts(db, DAILY_SCOPE, "2026-09-07")
    assert counts == {"acct-1": 3, "acct-2": 1}


async def test_scopes_and_windows_are_isolated(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await bump_attempt_counter(db, QUOTA_REQUESTS_SCOPE, "row-1", "2026-09-07", 5)
    await bump_attempt_counter(db, DAILY_SCOPE, "row-1", "2026-09-07", 2)
    assert await get_attempt_counts(db, QUOTA_REQUESTS_SCOPE, "2026-09-07") == {"row-1": 5}
    assert await get_attempt_counts(db, DAILY_SCOPE, "2026-09-07") == {"row-1": 2}
    assert await get_attempt_counts(db, QUOTA_REQUESTS_SCOPE, "2026-09-08") == {}


async def test_bump_ignores_nonpositive_amounts(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await bump_attempt_counter(db, DAILY_SCOPE, "acct-1", "2026-09-07", 0)
    await bump_attempt_counter(db, DAILY_SCOPE, "acct-1", "2026-09-07", -3)
    assert await get_attempt_counts(db, DAILY_SCOPE, "2026-09-07") == {}


async def test_prune_removes_stale_windows_only(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await bump_attempt_counter(db, DAILY_SCOPE, "old-acct", "2026-01-01", 1)
    await bump_attempt_counter(db, DAILY_SCOPE, "new-acct", "2026-09-07", 1)
    async with get_connection(db) as conn:
        await conn.execute(
            "UPDATE attempt_counters SET updated_at = datetime('now', '-40 days') "
            "WHERE window_id = '2026-01-01'"
        )
        await conn.commit()
    await prune_attempt_counters(db)
    assert await get_attempt_counts(db, DAILY_SCOPE, "2026-01-01") == {}
    assert await get_attempt_counts(db, DAILY_SCOPE, "2026-09-07") == {"new-acct": 1}

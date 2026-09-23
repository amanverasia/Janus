import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from janus.storage.api_keys import create_key
from janus.storage.budgets import create_or_update_budget, get_budget_status, get_budgets
from janus.storage.database import get_connection, init_db
from janus.storage.settings import set_setting
from janus.storage.usage import record_usage
from tests.fixtures.usage_seed import seed_usage


@pytest.fixture
async def key_budget_db(tmp_path):
    db_path = tmp_path / "budgets.db"
    await init_db(db_path)
    _, key = await create_key(db_path, "limited-key")
    return db_path, key["id"]


async def test_absolute_only_counts_historical_spend_and_survives_restart(key_budget_db):
    db_path, key_id = key_budget_db
    now = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)
    _, other = await create_key(db_path, "other-key")
    await seed_usage(
        db_path,
        [
            {"timestamp": "2025-01-01 12:00:00", "cost": 8.0, "client_key_id": key_id},
            {"timestamp": "2026-09-23 12:00:00", "cost": 2.0, "client_key_id": key_id},
            {"timestamp": "2026-09-23 12:00:00", "cost": 100.0, "client_key_id": other["id"]},
            {"timestamp": "2026-09-23 12:00:00", "cost": 100.0},
        ],
    )
    await create_or_update_budget(db_path, key_id=key_id, absolute_limit=10)
    for timezone in ("UTC", "Asia/Kolkata", "America/New_York"):
        await set_setting(db_path, "server_reporting_timezone", timezone)
        await init_db(db_path)
        status = await get_budget_status(db_path, key_id=key_id, now=now)
        assert status["daily_limit"] is None
        assert status["daily_status"] is None
        assert status["remaining"] is None
        assert status["pct_used"] == 0
        assert status["total_spend"] == 10
        assert status["absolute_pct_used"] == 100
        assert status["absolute_remaining"] == 0
        assert status["absolute_status"] == status["status"] == "exceeded"
        assert status["retry_after"] is None
        assert status["resets_at"] is None
    next_day = await get_budget_status(db_path, key_id=key_id, now=now + timedelta(days=1))
    assert next_day["today_spend"] == 0
    assert next_day["total_spend"] == 10
    assert next_day["status"] == "exceeded"
    assert await get_budget_status(db_path, key_id=other["id"], now=now) is None


@pytest.mark.parametrize(
    ("today", "past", "daily_status", "absolute_status", "status"),
    [
        (1, 2, "ok", "ok", "ok"),
        (4, 2, "warning", "ok", "warning"),
        (1, 7, "ok", "warning", "warning"),
        (5, 0, "exceeded", "ok", "exceeded"),
        (1, 9, "ok", "exceeded", "exceeded"),
        (5, 5, "exceeded", "exceeded", "exceeded"),
    ],
)
async def test_daily_and_absolute_limits_apply_independently(
    key_budget_db, today, past, daily_status, absolute_status, status
):
    db_path, key_id = key_budget_db
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=5, absolute_limit=10)
    await seed_usage(
        db_path,
        [
            {"timestamp": "2026-09-23 12:00:00", "cost": today, "client_key_id": key_id},
            {"timestamp": "2026-09-01 12:00:00", "cost": past, "client_key_id": key_id},
        ],
    )
    result = await get_budget_status(
        db_path, key_id=key_id, now=datetime(2026, 9, 23, 18, tzinfo=UTC)
    )
    assert result["status"] == status
    assert result["daily_status"] == daily_status
    assert result["absolute_status"] == absolute_status
    assert result["today_spend"] == today
    assert result["total_spend"] == today + past
    assert (result["retry_after"] is None) == (absolute_status == "exceeded")


async def test_updates_preserve_other_limit_and_warning_threshold(key_budget_db):
    db_path, key_id = key_budget_db
    budget_id = await create_or_update_budget(
        db_path, key_id=key_id, daily_limit=5, absolute_limit=10, warn_pct=90
    )
    assert await create_or_update_budget(db_path, key_id=key_id, absolute_limit=20) == budget_id
    budget = (await get_budgets(db_path))[0]
    assert (budget["daily_limit"], budget["absolute_limit"], budget["warn_pct"]) == (5, 20, 90)
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=6)
    budget = (await get_budgets(db_path))[0]
    assert (budget["daily_limit"], budget["absolute_limit"], budget["warn_pct"]) == (6, 20, 90)
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=None)
    budget = (await get_budgets(db_path))[0]
    assert budget["daily_limit"] is None
    assert budget["absolute_limit"] == 20
    assert await create_or_update_budget(db_path, key_id=key_id, absolute_limit=None) is None
    assert await get_budgets(db_path) == []
    assert await get_budget_status(db_path, key_id=key_id) is None


async def test_clearing_absolute_keeps_daily_and_readding_counts_existing_usage(key_budget_db):
    db_path, key_id = key_budget_db
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=5, absolute_limit=10)
    await seed_usage(
        db_path, [{"timestamp": "2025-01-01 00:00:00", "cost": 11, "client_key_id": key_id}]
    )
    await create_or_update_budget(db_path, key_id=key_id, absolute_limit=None)
    result = await get_budget_status(db_path, key_id=key_id)
    assert result["daily_limit"] == 5
    assert result["absolute_limit"] is None
    assert result["total_spend"] is None
    assert result["absolute_remaining"] is None
    assert result["absolute_pct_used"] is None
    assert result["absolute_status"] is None
    assert result["status"] == "ok"
    await create_or_update_budget(db_path, key_id=key_id, absolute_limit=10)
    assert (await get_budget_status(db_path, key_id=key_id))["status"] == "exceeded"
    await create_or_update_budget(db_path, key_id=key_id, absolute_limit=20)
    result = await get_budget_status(db_path, key_id=key_id)
    assert result["total_spend"] == 11
    assert result["status"] == "ok"


async def test_completed_usage_reduces_absolute_remaining(key_budget_db):
    db_path, key_id = key_budget_db
    await create_or_update_budget(db_path, key_id=key_id, absolute_limit=2)
    await record_usage(
        db_path,
        provider_id="test",
        model="test/model",
        input_tokens=100,
        output_tokens=20,
        status=200,
        cost=0.5,
        client_key_id=key_id,
    )
    result = await get_budget_status(db_path, key_id=key_id)
    assert result["total_spend"] == 0.5
    assert result["absolute_remaining"] == 1.5


async def test_concurrent_budget_changes_keep_both_limits(key_budget_db):
    db_path, key_id = key_budget_db
    ids = await asyncio.gather(
        create_or_update_budget(db_path, key_id=key_id, daily_limit=5),
        create_or_update_budget(db_path, key_id=key_id, absolute_limit=10),
    )
    budgets = await get_budgets(db_path)
    assert len(budgets) == 1
    assert ids[0] == ids[1] == budgets[0]["id"]
    assert budgets[0]["daily_limit"] == 5
    assert budgets[0]["absolute_limit"] == 10


@pytest.mark.parametrize("field", ["daily_limit", "absolute_limit", "warn_pct"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf")])
async def test_invalid_budget_values_do_not_mutate(key_budget_db, field, value):
    db_path, key_id = key_budget_db
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=5, absolute_limit=10)
    before = await get_budgets(db_path)
    with pytest.raises(ValueError):
        await create_or_update_budget(db_path, key_id=key_id, **{field: value})
    assert await get_budgets(db_path) == before


async def test_absolute_budget_requires_existing_specific_key(key_budget_db):
    db_path, _key_id = key_budget_db
    with pytest.raises(ValueError, match="specific API key"):
        await create_or_update_budget(db_path, key_id=None, absolute_limit=10)
    with pytest.raises(ValueError, match="does not exist"):
        await create_or_update_budget(db_path, key_id=99999, absolute_limit=10)
    assert await get_budgets(db_path) == []


async def test_absolute_spend_query_uses_covering_key_index(key_budget_db):
    db_path, key_id = key_budget_db
    async with get_connection(db_path) as db:
        async with db.execute(
            "EXPLAIN QUERY PLAN SELECT COALESCE(SUM(cost), 0.0) FROM usage WHERE client_key_id = ?",
            (key_id,),
        ) as cursor:
            plan = await cursor.fetchall()
    assert any("COVERING INDEX idx_usage_key_cost" in row[3] for row in plan)

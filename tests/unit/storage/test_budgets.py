import datetime

import pytest

from janus.storage.budgets import (
    create_or_update_budget,
    delete_budget,
    get_budget_status,
    get_budgets,
)
from janus.storage.database import init_db
from tests.fixtures.usage_seed import seed_usage


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


@pytest.mark.asyncio
async def test_create_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    budget_id = await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    budgets = await get_budgets(db_path)
    assert len(budgets) == 1
    assert budgets[0]["daily_limit"] == 5.0
    assert budgets[0]["id"] == budget_id


@pytest.mark.asyncio
async def test_update_budget_replaces(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    await create_or_update_budget(db_path, key_id=None, daily_limit=10.0, warn_pct=90)
    budgets = await get_budgets(db_path)
    assert len(budgets) == 1
    assert budgets[0]["daily_limit"] == 10.0
    assert budgets[0]["warn_pct"] == 90


@pytest.mark.asyncio
async def test_budget_status_no_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    status = await get_budget_status(db_path, key_id=None)
    assert status is None


@pytest.mark.asyncio
async def test_budget_status_ok(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=10.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 3.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status is not None
    assert status["status"] == "ok"
    assert abs(status["today_spend"] - 3.0) < 0.0001
    assert abs(status["daily_limit"] - 10.0) < 0.0001


@pytest.mark.asyncio
async def test_budget_status_warning(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=10.0, warn_pct=50)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 6.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status["status"] == "warning"


@pytest.mark.asyncio
async def test_budget_status_exceeded(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(0), "cost": 6.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status["status"] == "exceeded"


@pytest.mark.asyncio
async def test_per_key_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    from janus.storage.api_keys import create_key
    _, key_record = await create_key(db_path, name="test-key")
    key_id = key_record["id"]
    await create_or_update_budget(db_path, key_id=key_id, daily_limit=2.0, warn_pct=80)
    await seed_usage(
        db_path,
        [{"timestamp": _ts(0), "cost": 1.0, "status": 200, "client_key_id": key_id}],
    )
    status = await get_budget_status(db_path, key_id=key_id)
    assert status is not None
    assert status["status"] == "ok"
    assert abs(status["today_spend"] - 1.0) < 0.0001


@pytest.mark.asyncio
async def test_only_today_counts(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    await seed_usage(db_path, [{"timestamp": _ts(2), "cost": 10.0, "status": 200}])
    status = await get_budget_status(db_path, key_id=None)
    assert status["status"] == "ok"
    assert abs(status["today_spend"] - 0.0) < 0.0001


@pytest.mark.asyncio
async def test_delete_budget(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    budget_id = await create_or_update_budget(db_path, key_id=None, daily_limit=5.0, warn_pct=80)
    deleted = await delete_budget(db_path, budget_id)
    assert deleted is True
    budgets = await get_budgets(db_path)
    assert len(budgets) == 0

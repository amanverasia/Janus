import datetime

import pytest

from janus.storage.analytics import (
    get_breakdown,
    get_spend_summary,
    get_success_rate,
)
from janus.storage.database import init_db
from tests.fixtures.usage_seed import seed_usage


def _ts(days_ago: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


@pytest.mark.asyncio
async def test_get_spend_summary_empty(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    result = await get_spend_summary(db_path, days=30)
    assert result["total_cost"] == 0.0
    assert result["total_requests"] == 0
    assert result["daily"] == []


@pytest.mark.asyncio
async def test_get_spend_summary_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {
                "timestamp": _ts(0),
                "model": "gpt-4o",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cost": 0.01,
                "status": 200,
            },
            {
                "timestamp": _ts(1),
                "model": "gpt-4o",
                "input_tokens": 2000,
                "output_tokens": 1000,
                "cost": 0.02,
                "status": 200,
            },
            {
                "timestamp": _ts(0),
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 500,
                "output_tokens": 250,
                "cost": 0.005,
                "status": 500,
            },
        ],
    )
    result = await get_spend_summary(db_path, days=30)
    assert result["total_requests"] == 3
    assert abs(result["total_cost"] - 0.035) < 0.0001
    assert result["total_input_tokens"] == 3500
    assert result["total_output_tokens"] == 1750
    assert len(result["daily"]) >= 1


@pytest.mark.asyncio
async def test_get_breakdown_by_model(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {
                "timestamp": _ts(0),
                "model": "gpt-4o",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cost": 0.01,
                "status": 200,
            },
            {
                "timestamp": _ts(0),
                "model": "gpt-4o",
                "input_tokens": 500,
                "output_tokens": 250,
                "cost": 0.005,
                "status": 200,
            },
            {
                "timestamp": _ts(0),
                "model": "claude",
                "input_tokens": 300,
                "output_tokens": 100,
                "cost": 0.003,
                "status": 200,
            },
        ],
    )
    result = await get_breakdown(db_path, dimension="model", days=30)
    assert len(result) == 2
    gpt = [r for r in result if r["model"] == "gpt-4o"][0]
    assert gpt["requests"] == 2
    assert abs(gpt["cost"] - 0.015) < 0.0001


@pytest.mark.asyncio
async def test_get_breakdown_by_provider(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {
                "timestamp": _ts(0),
                "provider_id": "openai",
                "model": "gpt-4o",
                "cost": 0.01,
                "status": 200,
            },
            {
                "timestamp": _ts(0),
                "provider_id": "anthropic",
                "model": "claude",
                "cost": 0.02,
                "status": 200,
            },
        ],
    )
    result = await get_breakdown(db_path, dimension="provider", days=30)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_success_rate(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await seed_usage(
        db_path,
        [
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 200},
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 200},
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 500},
            {"timestamp": _ts(0), "model": "gpt-4o", "status": 429},
        ],
    )
    result = await get_success_rate(db_path, days=30)
    assert result["success_2xx"] == 2
    assert result["client_4xx"] == 1
    assert result["server_5xx"] == 1
    assert result["total"] == 4

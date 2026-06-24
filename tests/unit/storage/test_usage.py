import pytest

from janus.storage.database import init_db
from janus.storage.usage import get_usage_stats, record_usage


@pytest.mark.asyncio
async def test_record_usage(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1
    assert stats["total_input_tokens"] == 100
    assert stats["total_output_tokens"] == 50


@pytest.mark.asyncio
async def test_record_multiple_usage(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="an",
        model="claude",
        input_tokens=200,
        output_tokens=100,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=50,
        output_tokens=25,
        status=429,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 3
    assert stats["total_input_tokens"] == 350
    assert stats["total_output_tokens"] == 175


@pytest.mark.asyncio
async def test_usage_stats_by_model(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=200,
        output_tokens=100,
        status=200,
    )
    await record_usage(
        db_path,
        provider_id="an",
        model="claude",
        input_tokens=50,
        output_tokens=25,
        status=200,
    )
    stats = await get_usage_stats(db_path)
    by_model = {m["model"]: m for m in stats["by_model"]}
    assert by_model["glm-4.7"]["requests"] == 2
    assert by_model["glm-4.7"]["input_tokens"] == 300
    assert by_model["claude"]["requests"] == 1


@pytest.mark.asyncio
async def test_record_usage_with_cost_and_cache(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="an",
        model="claude-sonnet-4-20250514",
        account_id="an-0",
        input_tokens=1000,
        output_tokens=500,
        cache_creation_tokens=200,
        cache_read_tokens=800,
        status=200,
        client_key_id=1,
        cost=0.015,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1


@pytest.mark.asyncio
async def test_record_usage_defaults_backward_compatible(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="glm",
        model="glm-4.7",
        input_tokens=100,
        output_tokens=50,
        status=200,
    )
    stats = await get_usage_stats(db_path)
    assert stats["total_requests"] == 1

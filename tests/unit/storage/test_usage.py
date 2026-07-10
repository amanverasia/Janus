import pytest

from janus.storage.database import init_db
from janus.storage.usage import get_unpriced_models, get_usage_stats, record_usage


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
async def test_record_usage_with_client_key_label(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        model="gpt-4o",
        status=200,
        client_key_label="Config (sk-s****tatic)",
        cost=0.01,
    )
    from janus.storage.analytics import get_breakdown

    rows = await get_breakdown(db_path, dimension="client_key", days=30)
    assert rows[0]["client_key"] == "Config (sk-s****tatic)"


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


# --- get_unpriced_models ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_unpriced_models_finds_zero_cost_with_tokens(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="p",
        model="mystery-model",
        input_tokens=100,
        output_tokens=50,
        status=200,
        cost=0.0,
    )
    rows = await get_unpriced_models(db_path)
    assert len(rows) == 1
    assert rows[0]["model"] == "mystery-model"
    assert rows[0]["requests"] == 1
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 50


@pytest.mark.asyncio
async def test_get_unpriced_models_excludes_priced_models(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="p",
        model="priced-model",
        input_tokens=100,
        output_tokens=50,
        status=200,
        cost=1.5,
    )
    rows = await get_unpriced_models(db_path)
    assert rows == []


@pytest.mark.asyncio
async def test_get_unpriced_models_excludes_zero_token_rows(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path,
        provider_id="p",
        model="empty-model",
        input_tokens=0,
        output_tokens=0,
        status=200,
        cost=0.0,
    )
    rows = await get_unpriced_models(db_path)
    assert rows == []


@pytest.mark.asyncio
async def test_get_unpriced_models_ordered_by_tokens_desc(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path, provider_id="p", model="small", input_tokens=10, output_tokens=5, status=200
    )
    await record_usage(
        db_path,
        provider_id="p",
        model="big",
        input_tokens=1000,
        output_tokens=500,
        status=200,
    )
    rows = await get_unpriced_models(db_path)
    assert [r["model"] for r in rows] == ["big", "small"]


@pytest.mark.asyncio
async def test_get_unpriced_models_respects_days_window(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    from janus.storage.database import get_connection

    await record_usage(
        db_path, provider_id="p", model="old-model", input_tokens=10, output_tokens=5, status=200
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE usage SET timestamp = datetime('now', '-90 days') WHERE model = 'old-model'"
        )
        await db.commit()
    rows = await get_unpriced_models(db_path, days=30)
    assert rows == []
    rows_all = await get_unpriced_models(db_path, days=120)
    assert len(rows_all) == 1


@pytest.mark.asyncio
async def test_get_unpriced_models_partial_cost_mix_excluded(tmp_path):
    # A model with some $0 rows (old, pre-sync) and some priced rows nets a
    # nonzero SUM(cost) -- it should NOT show up as "unpriced".
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await record_usage(
        db_path, provider_id="p", model="mixed", input_tokens=10, output_tokens=5, cost=0.0
    )
    await record_usage(
        db_path, provider_id="p", model="mixed", input_tokens=20, output_tokens=10, cost=0.5
    )
    rows = await get_unpriced_models(db_path)
    assert rows == []

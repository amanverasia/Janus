import pytest

from janus.storage.database import init_db
from janus.storage.pricing_db import (
    create_or_update_pricing_override,
    delete_pricing_override,
    get_pricing_overrides,
    list_pricing_overrides,
)


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_override(db):
    await create_or_update_pricing_override(
        db,
        {
            "model": "custom-model",
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.5,
            "cache_read_per_mtok": 0.1,
        },
    )
    overrides = await list_pricing_overrides(db)
    assert len(overrides) == 1
    assert overrides[0]["model"] == "custom-model"
    assert overrides[0]["input_per_mtok"] == 1.0


async def test_update_override(db):
    await create_or_update_pricing_override(
        db,
        {
            "model": "m",
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
    )
    await create_or_update_pricing_override(
        db,
        {
            "model": "m",
            "input_per_mtok": 5.0,
            "output_per_mtok": 10.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
    )
    overrides = await list_pricing_overrides(db)
    assert len(overrides) == 1
    assert overrides[0]["input_per_mtok"] == 5.0


async def test_delete_override(db):
    await create_or_update_pricing_override(
        db,
        {
            "model": "m",
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
    )
    await delete_pricing_override(db, "m")
    assert await list_pricing_overrides(db) == []


async def test_get_overrides_as_dict(db):
    await create_or_update_pricing_override(
        db,
        {
            "model": "m1",
            "input_per_mtok": 1.0,
            "output_per_mtok": 2.0,
            "cache_creation_per_mtok": 0.0,
            "cache_read_per_mtok": 0.0,
        },
    )
    result = await get_pricing_overrides(db)
    assert "m1" in result
    assert result["m1"]["input_per_mtok"] == 1.0

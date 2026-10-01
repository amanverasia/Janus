from __future__ import annotations

import pytest

from janus.storage.database import init_db
from janus.storage.model_overrides import (
    delete_override,
    get_override_map,
    invalidate_override_cache,
    list_overrides,
    upsert_override,
)


@pytest.mark.asyncio
async def test_upsert_list_delete_roundtrip(tmp_path):
    db_path = tmp_path / "o.db"
    await init_db(db_path)
    created = await upsert_override(db_path, "deepseek-chat", 0.9, "our evals prefer it")
    assert created["model"] == "deepseek-chat"
    rows = await list_overrides(db_path)
    assert len(rows) == 1
    assert rows[0]["quality"] == 0.9
    assert rows[0]["note"] == "our evals prefer it"

    await upsert_override(db_path, "deepseek-chat", 0.1)
    rows = await list_overrides(db_path)
    assert len(rows) == 1
    assert rows[0]["quality"] == 0.1

    assert await delete_override(db_path, "deepseek-chat") is True
    assert await delete_override(db_path, "deepseek-chat") is False
    assert await list_overrides(db_path) == []


@pytest.mark.asyncio
async def test_upsert_validates_input(tmp_path):
    db_path = tmp_path / "o.db"
    await init_db(db_path)
    with pytest.raises(ValueError):
        await upsert_override(db_path, "  ", 0.5)
    with pytest.raises(ValueError):
        await upsert_override(db_path, "m", 1.5)
    with pytest.raises(ValueError):
        await upsert_override(db_path, "m", -0.1)


@pytest.mark.asyncio
async def test_override_map_cached_and_invalidated(tmp_path):
    db_path = tmp_path / "o.db"
    await init_db(db_path)
    await upsert_override(db_path, "m1", 0.7)
    mapping = await get_override_map(db_path)
    assert mapping == {"m1": 0.7}
    await upsert_override(db_path, "m2", 0.8)
    mapping = await get_override_map(db_path)
    assert "m2" in mapping
    invalidate_override_cache()
    assert await get_override_map(db_path) == {"m1": 0.7, "m2": 0.8}

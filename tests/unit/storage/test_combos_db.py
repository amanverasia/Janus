import json

import pytest

from janus.storage.combos_db import (
    create_combo,
    delete_combo,
    get_combo,
    list_combos,
    update_combo,
)
from janus.storage.database import init_db


@pytest.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


async def test_create_and_list_combo(db):
    await create_combo(db, {"name": "best-effort", "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]})
    combos = await list_combos(db)
    assert len(combos) == 1
    assert combos[0]["name"] == "best-effort"
    assert json.loads(combos[0]["models"]) == ["openai/gpt-4o", "anthropic/claude-sonnet-4-20250514"]


async def test_get_combo(db):
    await create_combo(db, {"name": "test", "models": ["a/b"]})
    c = await get_combo(db, 1)
    assert c["name"] == "test"


async def test_get_combo_not_found(db):
    assert await get_combo(db, 999) is None


async def test_update_combo(db):
    await create_combo(db, {"name": "test", "models": ["a/b"]})
    await update_combo(db, 1, {"name": "test", "models": ["a/b", "c/d"]})
    c = await get_combo(db, 1)
    assert json.loads(c["models"]) == ["a/b", "c/d"]


async def test_delete_combo(db):
    await create_combo(db, {"name": "test", "models": ["a/b"]})
    await delete_combo(db, 1)
    assert await get_combo(db, 1) is None

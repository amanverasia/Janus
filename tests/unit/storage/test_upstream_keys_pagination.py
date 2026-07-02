import pytest

from janus.storage.database import init_db
from janus.storage.inventory_overview import get_best_upstream_keys, get_top_keys_per_provider
from janus.storage.upstream_keys import (
    count_upstream_keys_filtered,
    create_upstream_key,
    list_upstream_keys_page,
    update_upstream_key,
)


@pytest.mark.asyncio
async def test_list_upstream_keys_page_sort_and_paginate(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-page-sort-low")
    high = await create_upstream_key(db_path, provider_id="groq", key_value="gsk_" + "a" * 16)
    await update_upstream_key(
        db_path,
        high["id"],
        {"status": "active", "is_valid": 1, "credits_remaining": 99.0},
    )
    low = await create_upstream_key(
        db_path, provider_id="openai", key_value="sk-proj-page-sort-high"
    )
    await update_upstream_key(
        db_path,
        low["id"],
        {"status": "active", "is_valid": 1, "credits_remaining": 1.0},
    )

    total = await count_upstream_keys_filtered(db_path)
    assert total == 3

    page = await list_upstream_keys_page(
        db_path, sort="credits", direction="desc", limit=2, offset=0
    )
    assert len(page) == 2
    assert page[0]["credits_remaining"] == 99.0
    assert "key_value" not in page[0]

    second = await list_upstream_keys_page(
        db_path, sort="credits", direction="desc", limit=2, offset=2
    )
    assert len(second) == 1


@pytest.mark.asyncio
async def test_get_best_upstream_keys_one_per_provider(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    first = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-best-one")
    second = await create_upstream_key(db_path, provider_id="openai", key_value="sk-proj-best-two")
    await update_upstream_key(
        db_path,
        first["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1, "credits_remaining": 10.0},
    )
    await update_upstream_key(
        db_path,
        second["id"],
        {"status": "active", "is_valid": 1, "is_usable": 1, "credits_remaining": 20.0},
    )

    best = await get_best_upstream_keys(db_path)
    assert len(best) == 1
    assert best[0]["id"] == second["id"]
    assert best[0]["key_value"] == "sk-proj-best-two"


@pytest.mark.asyncio
async def test_get_top_keys_per_provider(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    for idx, credits in enumerate([5.0, 15.0, 25.0], start=1):
        record = await create_upstream_key(
            db_path,
            provider_id="openai",
            key_value=f"sk-proj-top-keys-{idx:02d}",
        )
        await update_upstream_key(
            db_path,
            record["id"],
            {"status": "active", "is_valid": 1, "is_usable": 1, "credits_remaining": credits},
        )

    grouped = await get_top_keys_per_provider(db_path, per_provider=2)
    assert len(grouped["openai"]) == 2
    assert grouped["openai"][0]["credits_remaining"] == 25.0

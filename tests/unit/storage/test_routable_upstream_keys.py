import pytest

from janus.storage.database import init_db
from janus.storage.upstream_keys import (
    create_upstream_key,
    list_routable_upstream_keys,
    update_upstream_key,
)


@pytest.mark.asyncio
async def test_list_routable_upstream_keys_filters_unusable(tmp_path):
    db_path = tmp_path / "janus.db"
    await init_db(db_path)

    active = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-active",
    )
    await update_upstream_key(
        db_path,
        active["id"],
        {
            "status": "active",
            "is_valid": 1,
            "is_usable": 1,
            "priority": 10,
            "credits_remaining": 5.0,
        },
    )

    invalid = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-invalid",
    )
    await update_upstream_key(
        db_path,
        invalid["id"],
        {"status": "invalid", "is_valid": 0, "is_usable": 0},
    )

    high_priority = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-high",
    )
    await update_upstream_key(
        db_path,
        high_priority["id"],
        {
            "status": "active",
            "is_valid": 1,
            "is_usable": 1,
            "priority": 100,
            "credits_remaining": 1.0,
        },
    )

    routable = await list_routable_upstream_keys(db_path, "openai")
    assert [key["id"] for key in routable] == [high_priority["id"], active["id"]]

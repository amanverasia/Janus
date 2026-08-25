import pytest

from janus.storage.database import get_connection, init_db
from janus.storage.inventory_overview import get_recent_activity
from janus.storage.upstream_keys import (
    create_upstream_key,
    list_upstream_key_history,
    record_upstream_key_history,
)


@pytest.mark.asyncio
async def test_history_write_ignores_noop_but_keeps_initial_transition(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-history",
    )

    await record_upstream_key_history(
        db_path,
        upstream_key_id=key["id"],
        previous_status="active",
        new_status="active",
        credits_remaining=9.0,
    )
    await record_upstream_key_history(
        db_path,
        upstream_key_id=key["id"],
        previous_status=None,
        new_status="pending_validation",
        credits_remaining=None,
    )

    history = await list_upstream_key_history(db_path, key["id"])
    assert len(history) == 1
    assert history[0]["previous_status"] is None
    assert history[0]["new_status"] == "pending_validation"


@pytest.mark.asyncio
async def test_history_queries_hide_legacy_noop_rows(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-legacy-history",
    )
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO upstream_key_history
               (upstream_key_id, previous_status, new_status, credits_remaining)
               VALUES (?, 'active', 'invalid', NULL),
                      (?, 'active', 'active', 11.0)""",
            (key["id"], key["id"]),
        )
        await db.commit()

    detail_history = await list_upstream_key_history(db_path, key["id"])
    recent_activity = await get_recent_activity(db_path)

    assert [(item["previous_status"], item["new_status"]) for item in detail_history] == [
        ("active", "invalid")
    ]
    assert [(item["previous_status"], item["new_status"]) for item in recent_activity] == [
        ("active", "invalid")
    ]
    assert detail_history[0]["credits_remaining"] is None
    assert detail_history[0]["provider_billing_model"] == "postpaid"

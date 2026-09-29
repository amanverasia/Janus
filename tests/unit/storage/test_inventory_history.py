import pytest

from janus.storage.database import get_connection, init_db
from janus.storage.inventory_overview import (
    UPSTREAM_KEY_HISTORY_RETENTION_DAYS,
    get_recent_activity,
    prune_upstream_key_history,
)
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


async def test_recent_activity_matches_legacy_predicate(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-parity",
    )
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO upstream_key_history
               (upstream_key_id, previous_status, new_status, credits_remaining, changed_at)
               VALUES
                 (?, 'active', 'active', 1.0, '2026-01-01 00:00:00'),
                 (?, NULL, 'pending_validation', NULL, '2026-01-02 00:00:00'),
                 (?, 'pending_validation', 'active', 2.0, '2026-01-03 00:00:00'),
                 (?, 'active', 'invalid', 3.0, '2026-01-04 00:00:00')""",
            (key["id"], key["id"], key["id"], key["id"]),
        )
        await db.commit()

    recent = await get_recent_activity(db_path)
    async with get_connection(db_path) as db:
        async with db.execute(
            """SELECT h.id
               FROM upstream_key_history h
               JOIN upstream_keys k ON h.upstream_key_id = k.id
               JOIN inventory_providers p ON k.provider_id = p.id
               WHERE h.previous_status IS NULL OR h.previous_status != h.new_status
               ORDER BY h.changed_at DESC
               LIMIT 20"""
        ) as cur:
            legacy_ids = [row["id"] for row in await cur.fetchall()]

    assert [row["id"] for row in recent] == legacy_ids
    assert [(row["previous_status"], row["new_status"]) for row in recent] == [
        ("active", "invalid"),
        ("pending_validation", "active"),
        (None, "pending_validation"),
    ]


async def test_recent_activity_uses_changed_at_index(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    async with get_connection(db_path) as db:
        async with db.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id FROM upstream_key_history "
            "WHERE previous_status IS NOT new_status "
            "ORDER BY changed_at DESC LIMIT 20"
        ) as cur:
            plan = "\n".join(row[3] for row in await cur.fetchall())
    assert "idx_upstream_key_history_changed_at" in plan


async def test_prune_upstream_key_history_keeps_recent_rows(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    key = await create_upstream_key(
        db_path,
        provider_id="openai",
        key_value="sk-proj-prune",
    )
    async with get_connection(db_path) as db:
        await db.execute(
            """INSERT INTO upstream_key_history
               (upstream_key_id, previous_status, new_status, changed_at)
               VALUES (?, 'active', 'invalid', '2020-01-01 00:00:00')""",
            (key["id"],),
        )
        await db.commit()
    await record_upstream_key_history(
        db_path,
        upstream_key_id=key["id"],
        previous_status="invalid",
        new_status="active",
    )

    deleted = await prune_upstream_key_history(db_path, UPSTREAM_KEY_HISTORY_RETENTION_DAYS)

    assert deleted == 1
    history = await list_upstream_key_history(db_path, key["id"])
    assert [row["new_status"] for row in history] == ["active"]

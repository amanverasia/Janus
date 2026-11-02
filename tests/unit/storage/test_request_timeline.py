import json

from janus.storage.attempt_signals import record_attempt_signal
from janus.storage.database import get_connection, init_db
from janus.storage.outcomes import record_request_outcome
from janus.storage.request_logs import record_request_log
from janus.storage.request_timeline import get_request_timeline
from janus.storage.upstream_keys import create_upstream_key


async def _signal(db, request_id, order, **overrides):
    values = {
        "request_id": request_id,
        "attempt_order": order,
        "model": "openai/m1",
        "account_id": "account-a",
        "provider_id": "openai::uk_account-a",
        "client_format": "openai",
        "streamed": False,
        "outcome": "error",
        "status": 500,
        "ttft_ms": None,
        "duration_ms": 25,
        "output_tokens": 0,
        "output_tps": None,
        "failure_class": "server_error",
        "cooldown_decision": "applied",
        "cooldown_seconds": 30,
    }
    values.update(overrides)
    await record_attempt_signal(db, **values)


async def test_correlation_migration_is_idempotent_and_legacy_rows_stay_uncorrelated(tmp_path):
    db = tmp_path / "timeline.db"
    await init_db(db)
    await init_db(db)
    async with get_connection(db) as conn:
        for table, expected in {
            "request_logs": {"request_id"},
            "request_outcomes": {"request_id"},
            "attempt_signals": {
                "request_id",
                "attempt_order",
                "failure_class",
                "cooldown_decision",
                "cooldown_seconds",
            },
        }.items():
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                columns = {row[1] for row in await cur.fetchall()}
            assert expected <= columns
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_attempt_signals_request_order",),
        ) as cur:
            assert await cur.fetchone() is not None

    await record_request_log(db, model="legacy", status=503, error="old row")
    detail = await get_request_timeline(db, 1)
    assert detail is not None
    assert detail["model"] == "legacy"
    assert detail["correlation"] == {"available": False, "request_id": None}
    assert detail["attempts"] == []
    assert detail["attempt_pagination"]["total"] == 0
    assert detail["final_outcome"] == "upstream_failure"


async def test_pre_correlation_schema_upgrades_without_backfilling_ids(tmp_path):
    db = tmp_path / "timeline.db"
    await init_db(db)
    async with get_connection(db) as conn:
        await conn.execute("DROP INDEX idx_request_logs_request_id")
        await conn.execute("DROP INDEX idx_request_outcomes_request_id")
        await conn.execute("DROP INDEX idx_attempt_signals_request_order")
        await conn.execute("ALTER TABLE request_logs DROP COLUMN request_id")
        await conn.execute("ALTER TABLE request_outcomes DROP COLUMN request_id")
        await conn.execute("ALTER TABLE attempt_signals DROP COLUMN request_id")
        await conn.execute("ALTER TABLE attempt_signals DROP COLUMN attempt_order")
        await conn.execute("ALTER TABLE attempt_signals DROP COLUMN failure_class")
        await conn.execute("ALTER TABLE attempt_signals DROP COLUMN cooldown_decision")
        await conn.execute("ALTER TABLE attempt_signals DROP COLUMN cooldown_seconds")
        await conn.execute("INSERT INTO request_logs (model, status) VALUES ('legacy', 503)")
        await conn.commit()

    await init_db(db)
    detail = await get_request_timeline(db, 1)
    assert detail is not None
    assert detail["request_id"] is None
    assert detail["correlation"]["available"] is False
    assert detail["attempts"] == []
    async with get_connection(db) as conn:
        async with conn.execute("PRAGMA table_info(attempt_signals)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        assert {
            "request_id",
            "attempt_order",
            "failure_class",
            "cooldown_decision",
            "cooldown_seconds",
        } <= columns
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_attempt_signals_request_order",),
        ) as cur:
            assert await cur.fetchone() is not None


async def test_timeline_is_ordered_paginated_masked_and_bounded(tmp_path):
    db = tmp_path / "timeline.db"
    await init_db(db)
    key = await create_upstream_key(
        db, provider_id="openai", key_value="sk-synthetic", key_label="Synthetic account"
    )
    request_id = "request-pagination"
    await record_request_log(
        db,
        request_id=request_id,
        model="openai/m1",
        status=200,
        request_body="private request",
        response_body="private response",
    )
    await record_request_outcome(db, request_id=request_id, model="m1", status=200, attempts=55)
    for order in range(1, 56):
        await _signal(
            db,
            request_id,
            order,
            account_id=key["id"],
            outcome="ok" if order == 55 else "error",
            status=200 if order == 55 else 401,
            failure_class=None if order == 55 else "auth_error",
            cooldown_decision="none" if order == 55 else "applied",
            cooldown_seconds=None if order == 55 else 300,
        )

    first = await get_request_timeline(db, 1)
    assert first is not None
    assert first["has_bodies"] is True
    assert first["correlation"] == {"available": True, "request_id": request_id}
    assert first["outcome"]["attempts"] == 55
    assert first["attempt_pagination"] == {
        "total": 55,
        "limit": 25,
        "offset": 0,
        "page": 1,
        "total_pages": 3,
    }
    assert [row["attempt_order"] for row in first["attempts"]] == list(range(1, 26))
    assert len(json.dumps(first["attempts"])) < 25_000
    serialized_detail = json.dumps(first)
    assert key["id"] not in serialized_detail
    assert "sk-synthetic" not in serialized_detail
    assert "request_body" not in first["attempts"][0]
    assert "response_body" not in first["attempts"][0]
    assert first["attempts"][0]["recovery"] == {
        "label": "Review credentials",
        "href": "/dashboard/ui/inventory/keys?provider_id=openai",
    }

    second = await get_request_timeline(db, 1, limit=25, offset=25)
    assert second is not None
    assert [row["attempt_order"] for row in second["attempts"]] == list(range(26, 51))
    last = await get_request_timeline(db, 1, limit=25, offset=999)
    assert last is not None
    assert last["attempt_pagination"]["offset"] == 50
    assert [row["attempt_order"] for row in last["attempts"]] == list(range(51, 56))
    assert last["attempts"][-1]["recovery"] is None
    assert last["final_outcome"] == "success"


async def test_masking_survives_missing_or_pruned_attempt_signals(tmp_path):
    db = tmp_path / "timeline.db"
    await init_db(db)
    error = "All providers exhausted (2 attempt(s)): acct-a: 401; acct-b: 402"
    for request_id in (None, "request-pruned"):
        await record_request_log(
            db,
            request_id=request_id,
            provider_id="acct-c",
            account_id="acct-c",
            status=503,
            error=error,
        )
        if request_id:
            await record_request_outcome(
                db,
                request_id=request_id,
                provider_id="acct-c",
                account_id="acct-c",
                status=503,
                attempts=2,
            )

    for log_id in (1, 2):
        detail = await get_request_timeline(db, log_id)
        assert detail is not None
        assert detail["attempts"] == []
        serialized = json.dumps(detail)
        assert all(account not in serialized for account in ("acct-a", "acct-b", "acct-c"))


async def test_client_validation_cancellation_and_upstream_client_error_are_distinct(tmp_path):
    db = tmp_path / "timeline.db"
    await init_db(db)
    cases = [
        ("validation", 400, None, "client_validation"),
        ("cancelled", 499, "aborted", "cancelled"),
        ("upstream-client", 400, "client_error", "upstream_client_error"),
    ]
    for index, (request_id, status, attempt_outcome, expected) in enumerate(cases, start=1):
        await record_request_log(db, request_id=request_id, model="m1", status=status)
        await record_request_outcome(db, request_id=request_id, model="m1", status=status)
        if attempt_outcome:
            await _signal(
                db,
                request_id,
                1,
                outcome=attempt_outcome,
                status=status,
                failure_class="cancelled" if attempt_outcome == "aborted" else "client_error",
                cooldown_decision="none",
                cooldown_seconds=None,
            )
        detail = await get_request_timeline(db, index)
        assert detail is not None
        assert detail["final_outcome"] == expected
        if attempt_outcome == "aborted":
            assert detail["attempts"][0]["failure_class"] == "cancelled"
            assert detail["attempts"][0]["recovery"] is None

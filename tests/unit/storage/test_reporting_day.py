from datetime import UTC, datetime

import pytest

from janus.storage.analytics import get_calendar_day_spend_summary
from janus.storage.api_keys import create_key
from janus.storage.budgets import create_or_update_budget, get_budget_status
from janus.storage.database import init_db
from janus.storage.settings import resolve_reporting_timezone, set_setting
from janus.storage.time_windows import calendar_day_window
from janus.storage.usage import get_today_total_cost
from tests.fixtures.usage_seed import seed_usage


def test_utc_calendar_day_boundaries() -> None:
    window = calendar_day_window("UTC", now=datetime(2026, 8, 25, 23, 59, tzinfo=UTC))
    assert window.start_utc == datetime(2026, 8, 25, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 8, 26, tzinfo=UTC)
    assert window.retry_after_seconds == 60


def test_invalid_stored_timezone_falls_back_to_utc() -> None:
    assert resolve_reporting_timezone({"server_reporting_timezone": "not/a-zone"}) == "UTC"


@pytest.mark.parametrize(
    ("now", "expected_start"),
    [
        (
            datetime(2026, 8, 24, 18, 29, 59, tzinfo=UTC),
            datetime(2026, 8, 23, 18, 30, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 24, 18, 30, tzinfo=UTC),
            datetime(2026, 8, 24, 18, 30, tzinfo=UTC),
        ),
    ],
)
def test_kolkata_midnight_boundaries(now: datetime, expected_start: datetime) -> None:
    window = calendar_day_window("Asia/Kolkata", now=now)
    assert window.start_utc == expected_start
    assert window.end_utc == expected_start.replace(day=expected_start.day + 1)


@pytest.mark.parametrize(
    ("now", "expected_start", "expected_end", "hours"),
    [
        (
            datetime(2026, 3, 8, 12, tzinfo=UTC),
            datetime(2026, 3, 8, 5, tzinfo=UTC),
            datetime(2026, 3, 9, 4, tzinfo=UTC),
            23,
        ),
        (
            datetime(2026, 11, 1, 12, tzinfo=UTC),
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
            25,
        ),
    ],
)
def test_new_york_dst_boundaries(
    now: datetime,
    expected_start: datetime,
    expected_end: datetime,
    hours: int,
) -> None:
    window = calendar_day_window("America/New_York", now=now)
    assert window.start_utc == expected_start
    assert window.end_utc == expected_end
    assert (window.end_utc - window.start_utc).total_seconds() == hours * 3600


async def test_overview_budget_and_backfill_totals_share_reporting_day(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    await set_setting(db_path, "server_reporting_timezone", "Asia/Kolkata")
    _, key = await create_key(db_path, "first")
    _, other_key = await create_key(db_path, "second")
    await create_or_update_budget(db_path, key_id=None, daily_limit=10, warn_pct=80)
    await create_or_update_budget(db_path, key_id=key["id"], daily_limit=10, warn_pct=80)
    await seed_usage(
        db_path,
        [
            {
                "timestamp": "2026-08-24 18:29:59",
                "cost": 8.0,
                "client_key_id": key["id"],
            },
            {
                "timestamp": "2026-08-24 18:30:00",
                "cost": 1.0,
                "client_key_id": key["id"],
            },
            {
                "timestamp": "2026-08-25 12:00:00",
                "cost": 2.0,
                "client_key_id": other_key["id"],
            },
            {
                "timestamp": "2026-08-25 18:30:00",
                "cost": 16.0,
                "client_key_id": key["id"],
            },
        ],
    )
    now = datetime(2026, 8, 25, 12, tzinfo=UTC)

    summary = await get_calendar_day_spend_summary(db_path, now=now)
    global_budget = await get_budget_status(db_path, key_id=None, now=now)
    key_budget = await get_budget_status(db_path, key_id=key["id"], now=now)
    backfill_total = await get_today_total_cost(db_path, now=now)

    assert summary["total_cost"] == 3.0
    assert summary["total_requests"] == 2
    assert global_budget is not None
    assert global_budget["today_spend"] == summary["total_cost"]
    assert global_budget["reporting_timezone"] == "Asia/Kolkata"
    assert key_budget is not None
    assert key_budget["today_spend"] == 1.0
    assert backfill_total == summary["total_cost"]

from datetime import UTC, datetime

import pytest

from janus.storage.database import init_db
from janus.storage.quotas import (
    describe_reset,
    get_window_usage,
    quota_status,
    window_id,
    window_reset,
    window_start,
)
from janus.storage.usage import record_usage

_NOW = datetime(2026, 7, 5, 14, 30, tzinfo=UTC)  # a Sunday


def test_window_id_stable_within_window():
    assert window_id("daily", _NOW) == "2026-07-05"
    assert window_id("weekly", _NOW) == "2026-W27"
    assert window_id("monthly", _NOW) == "2026-07"
    five_h = window_id("5h", _NOW)
    assert five_h == window_id("5h", _NOW.replace(minute=45))


def test_window_start_boundaries():
    assert window_start("daily", _NOW) == datetime(2026, 7, 5, tzinfo=UTC)
    assert window_start("weekly", _NOW) == datetime(2026, 6, 29, tzinfo=UTC)  # Monday
    assert window_start("monthly", _NOW) == datetime(2026, 7, 1, tzinfo=UTC)
    start_5h = window_start("5h", _NOW)
    assert 0 <= (_NOW - start_5h).total_seconds() < 5 * 3600


def test_window_reset_boundaries():
    assert window_reset("daily", _NOW) == datetime(2026, 7, 6, tzinfo=UTC)
    assert window_reset("weekly", _NOW) == datetime(2026, 7, 6, tzinfo=UTC)  # next Monday
    assert window_reset("monthly", _NOW) == datetime(2026, 8, 1, tzinfo=UTC)
    december = datetime(2026, 12, 15, tzinfo=UTC)
    assert window_reset("monthly", december) == datetime(2027, 1, 1, tzinfo=UTC)


def test_unknown_window_raises():
    with pytest.raises(ValueError):
        window_id("hourly")
    with pytest.raises(ValueError):
        window_start("hourly")
    with pytest.raises(ValueError):
        window_reset("hourly")


def test_describe_reset_countdown():
    info = describe_reset("daily", _NOW)
    assert info["resets_in"] == "9h 30m"
    assert info["resets_at"].startswith("2026-07-06")


async def test_get_window_usage_counts_row_and_expanded_ids(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    await record_usage(db, provider_id="copilot", input_tokens=10, output_tokens=5, status=200)
    await record_usage(
        db, provider_id="copilot::uk_abc", input_tokens=20, output_tokens=5, status=200
    )
    await record_usage(db, provider_id="other", input_tokens=100, output_tokens=0, status=200)
    usage = await get_window_usage(db, "copilot", "daily")
    assert usage["requests"] == 2
    assert usage["tokens"] == 40


async def test_get_window_usage_empty(tmp_path):
    db = tmp_path / "t.db"
    await init_db(db)
    usage = await get_window_usage(db, "copilot", "monthly")
    assert usage == {"requests": 0, "tokens": 0}


def test_quota_status_thresholds():
    assert quota_status(79, 100) == "ok"
    assert quota_status(80, 100) == "warning"
    assert quota_status(100, 100) == "exhausted"
    assert quota_status(0, 0) == "exhausted"

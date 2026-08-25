from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .settings import get_reporting_timezone


@dataclass(frozen=True)
class CalendarDayWindow:
    timezone: str
    now_utc: datetime
    start_utc: datetime
    end_utc: datetime

    @property
    def query_bounds(self) -> tuple[str, str]:
        return (_sqlite_timestamp(self.start_utc), _sqlite_timestamp(self.end_utc))

    @property
    def retry_after_seconds(self) -> int:
        return max(1, math.ceil((self.end_utc - self.now_utc).total_seconds()))


def calendar_day_window(
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> CalendarDayWindow:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current_utc = current.astimezone(UTC)
    timezone = ZoneInfo(timezone_name)
    local_date = current_utc.astimezone(timezone).date()
    start_local = datetime.combine(local_date, time.min, tzinfo=timezone)
    end_local = start_local + timedelta(days=1)
    return CalendarDayWindow(
        timezone=timezone_name,
        now_utc=current_utc,
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC),
    )


async def current_reporting_day(
    db_path: str | Path,
    *,
    now: datetime | None = None,
) -> CalendarDayWindow:
    timezone_name = await get_reporting_timezone(db_path)
    return calendar_day_window(timezone_name, now=now)


def _sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")

"""Built-in date/time source evaluated once per global jRULE cycle."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import state

OBJECT_NAME = "DateTime"
TIMEZONE_NAME = "Australia/Melbourne"


def properties_at(instant: datetime | None = None, timezone_name: str = TIMEZONE_NAME) -> dict:
    """Return typed calendar fields in the configured IANA timezone."""
    instant = instant or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("An aware datetime is required")
    local = instant.astimezone(ZoneInfo(timezone_name))
    weekday = local.isoweekday()  # Monday=1, Sunday=7
    return {
        "datetime": local.isoformat(timespec="seconds"),
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M:%S"),
        "timezone": timezone_name,
        "utc_offset": local.strftime("%z"),
        "year": local.year,
        "month": local.month,
        "month_name": local.strftime("%B"),
        "day": local.day,
        "hour": local.hour,
        "minute": local.minute,
        "second": local.second,
        "day_of_week": weekday,
        "weekday_name": local.strftime("%A"),
        "week_number": local.isocalendar().week,
        "day_of_year": local.timetuple().tm_yday,
        "quarter": (local.month - 1) // 3 + 1,
        "is_weekday": weekday <= 5,
        "is_weekend": weekday >= 6,
        "unix_timestamp": int(instant.timestamp()),
    }


async def poll_datetime() -> None:
    """Advance DateTime previous/current once before evaluating cycle rules."""
    await state.update_properties(OBJECT_NAME, properties_at())

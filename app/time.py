from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def db_now() -> datetime:
    """UTC naive datetime used by SQLite/Postgres DateTime columns."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def normalize(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def business_date(
    value: datetime,
    *,
    timezone_name: str = "Asia/Seoul",
    cutoff: str = "00:00",
) -> date:
    """Return the store-local business date, including a configurable closing cutoff."""

    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Asia/Seoul")
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    local_value = utc_value.astimezone(zone)
    try:
        cutoff_time = time.fromisoformat(cutoff)
    except ValueError:
        cutoff_time = time(0, 0)
    if local_value.time().replace(tzinfo=None) < cutoff_time:
        return local_value.date() - timedelta(days=1)
    return local_value.date()

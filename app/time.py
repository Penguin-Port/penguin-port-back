from datetime import datetime, timezone


def db_now() -> datetime:
    """UTC naive datetime used by SQLite/Postgres DateTime columns."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def normalize(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

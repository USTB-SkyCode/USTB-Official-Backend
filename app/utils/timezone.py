from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Config


def get_app_timezone():
    try:
        return ZoneInfo(Config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        return timezone.utc


def serialize_datetime_for_api(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(get_app_timezone()).isoformat(timespec='seconds')
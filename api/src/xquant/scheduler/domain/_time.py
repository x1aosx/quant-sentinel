from __future__ import annotations

from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def require_aware(value: datetime, field_name: str) -> datetime:
    """Return an aware datetime or raise when timezone information is missing."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def coerce_timezone(value: str | ZoneInfo | tzinfo) -> tzinfo:
    """Resolve an IANA timezone name into a ZoneInfo instance."""

    if isinstance(value, tzinfo):
        return value
    try:
        return ZoneInfo(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"invalid IANA timezone: {value!r}") from exc

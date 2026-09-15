from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from math import floor
from typing import Any
from zoneinfo import ZoneInfo

from ._time import coerce_timezone, require_aware


class Trigger(ABC):
    """Calculate the next fire time for a schedule."""

    timezone: tzinfo

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Return a stable, JSON-safe representation of this trigger."""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Trigger:
        """Restore a trigger from a serialized representation."""

        return load_trigger(payload)

    @abstractmethod
    def next_fire_time(
        self,
        previous_fire_time: datetime | None,
        now: datetime,
    ) -> datetime | None:
        """Return the first matching fire time after ``now``."""


@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool

    def matches(self, value: int) -> bool:
        return value in self.values


def _parse_cron_field(
    expression: str,
    minimum: int,
    maximum: int,
) -> _CronField:
    normalized = expression.strip()
    if not normalized:
        raise ValueError("cron field cannot be empty")

    values: set[int] = set()
    for part in normalized.split(","):
        item = part.strip()
        if not item:
            raise ValueError(f"invalid cron field: {expression!r}")

        base, separator, step_text = item.partition("/")
        if separator:
            if not step_text.isdigit():
                raise ValueError(f"invalid cron step: {item!r}")
            step = int(step_text)
            if step < 1:
                raise ValueError(f"cron step must be positive: {item!r}")
        else:
            step = 1

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"invalid cron range: {item!r}")
            start, end = int(start_text), int(end_text)
        elif base.isdigit():
            start = end = int(base)
        else:
            raise ValueError(f"invalid cron value: {item!r}")

        if start < minimum or end > maximum or start > end:
            raise ValueError(
                f"cron value {item!r} is outside {minimum}-{maximum}"
            )
        values.update(range(start, end + 1, step))

    return _CronField(frozenset(values), normalized == "*")


class CronTrigger(Trigger):
    """Five-field cron trigger: minute hour day month day_of_week."""

    def __init__(
        self,
        minute: str = "*",
        hour: str = "*",
        day: str = "*",
        month: str = "*",
        day_of_week: str = "*",
        *,
        timezone: str | ZoneInfo = "UTC",
        day_of_month: str | None = None,
    ) -> None:
        if day_of_month is not None:
            if day != "*":
                raise ValueError("use either day or day_of_month, not both")
            day = day_of_month

        self._expressions = {
            "minute": minute.strip(),
            "hour": hour.strip(),
            "day": day.strip(),
            "month": month.strip(),
            "day_of_week": day_of_week.strip(),
        }
        self.timezone = coerce_timezone(timezone)
        self.minute = _parse_cron_field(minute, 0, 59)
        self.hour = _parse_cron_field(hour, 0, 23)
        self.day = _parse_cron_field(day, 1, 31)
        self.month = _parse_cron_field(month, 1, 12)
        self.day_of_week = _parse_cron_field(day_of_week, 0, 7)
        self._hour_values = tuple(sorted(self.hour.values))
        self._minute_values = tuple(sorted(self.minute.values))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "cron",
            **self._expressions,
            "timezone": str(self.timezone),
        }

    def _matches_day(self, current_date: date) -> bool:
        day_matches = self.day.matches(current_date.day)
        cron_weekday = (current_date.weekday() + 1) % 7
        weekday_values = {
            0 if value == 7 else value for value in self.day_of_week.values
        }
        weekday_matches = cron_weekday in weekday_values

        if self.day.wildcard and self.day_of_week.wildcard:
            return True
        if self.day.wildcard:
            return weekday_matches
        if self.day_of_week.wildcard:
            return day_matches
        return day_matches or weekday_matches

    def next_fire_time(
        self,
        previous_fire_time: datetime | None,
        now: datetime,
    ) -> datetime | None:
        require_aware(now, "now")
        if previous_fire_time is not None:
            require_aware(previous_fire_time, "previous_fire_time")

        cutoff = now
        if previous_fire_time is not None and previous_fire_time > cutoff:
            cutoff = previous_fire_time
        local_cutoff = cutoff.astimezone(self.timezone)
        start_date = local_cutoff.date()

        for day_offset in range(366 * 8):
            candidate_date = start_date + timedelta(days=day_offset)
            if not self.month.matches(candidate_date.month):
                continue
            if not self._matches_day(candidate_date):
                continue

            for hour in self._hour_values:
                for minute in self._minute_values:
                    candidate = datetime(
                        candidate_date.year,
                        candidate_date.month,
                        candidate_date.day,
                        hour,
                        minute,
                        tzinfo=self.timezone,
                    )
                    if candidate > cutoff:
                        return candidate
        return None


class IntervalTrigger(Trigger):
    """Fire repeatedly at a fixed interval."""

    def __init__(
        self,
        interval_seconds: float | timedelta | None = None,
        *,
        seconds: float | timedelta | None = None,
        start_at: datetime | None = None,
        timezone: str | ZoneInfo | None = None,
    ) -> None:
        if interval_seconds is not None and seconds is not None:
            raise ValueError("provide interval_seconds or seconds, not both")
        interval_value = interval_seconds if interval_seconds is not None else seconds
        if interval_value is None:
            raise TypeError("interval_seconds is required")

        self.interval = (
            interval_value
            if isinstance(interval_value, timedelta)
            else timedelta(seconds=float(interval_value))
        )
        if self.interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if start_at is not None:
            require_aware(start_at, "start_at")
        self.start_at = start_at
        selected_timezone = timezone
        if selected_timezone is None and start_at is not None:
            selected_timezone = start_at.tzinfo
        self.timezone = coerce_timezone(selected_timezone or "UTC")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "interval",
            "interval_seconds": self.interval.total_seconds(),
            "start_at": self.start_at.isoformat() if self.start_at is not None else None,
            "timezone": str(self.timezone),
        }

    def next_fire_time(
        self,
        previous_fire_time: datetime | None,
        now: datetime,
    ) -> datetime | None:
        require_aware(now, "now")
        if previous_fire_time is not None:
            require_aware(previous_fire_time, "previous_fire_time")

        cutoff = now
        if previous_fire_time is not None and previous_fire_time > cutoff:
            cutoff = previous_fire_time

        anchor = self.start_at if self.start_at is not None else previous_fire_time
        if anchor is None:
            return now + self.interval
        if anchor > cutoff:
            return anchor

        steps = floor((cutoff - anchor) / self.interval) + 1
        return anchor + (self.interval * steps)


class DateTrigger(Trigger):
    """Fire once at an absolute datetime."""

    def __init__(
        self,
        run_at: datetime,
        *,
        timezone: str | ZoneInfo | None = None,
    ) -> None:
        require_aware(run_at, "run_at")
        self.run_at = run_at
        self.timezone = coerce_timezone(timezone or run_at.tzinfo or "UTC")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "date",
            "run_at": self.run_at.isoformat(),
            "timezone": str(self.timezone),
        }

    def next_fire_time(
        self,
        previous_fire_time: datetime | None,
        now: datetime,
    ) -> datetime | None:
        require_aware(now, "now")
        if previous_fire_time is not None:
            require_aware(previous_fire_time, "previous_fire_time")
        if previous_fire_time is not None and previous_fire_time >= self.run_at:
            return None
        return self.run_at if self.run_at >= now else None


class FixedDelayTrigger(Trigger):
    """Fire after a fixed delay from the previous fire time."""

    def __init__(
        self,
        delay_seconds: float | timedelta,
        *,
        start_at: datetime | None = None,
        timezone: str | ZoneInfo | None = None,
    ) -> None:
        self._delegate = IntervalTrigger(
            delay_seconds,
            start_at=start_at,
            timezone=timezone,
        )
        self.delay = self._delegate.interval
        self.start_at = start_at
        self.timezone = self._delegate.timezone

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "fixed_delay",
            "delay_seconds": self.delay.total_seconds(),
            "start_at": self.start_at.isoformat() if self.start_at is not None else None,
            "timezone": str(self.timezone),
        }

    def next_fire_time(
        self,
        previous_fire_time: datetime | None,
        now: datetime,
    ) -> datetime | None:
        return self._delegate.next_fire_time(previous_fire_time, now)


def load_trigger(payload: Mapping[str, Any] | Trigger) -> Trigger:
    """Restore a concrete trigger from a stable serialized mapping."""

    if isinstance(payload, Trigger):
        return payload
    if not isinstance(payload, Mapping):
        raise TypeError("trigger payload must be a mapping")

    trigger_type = str(payload.get("type") or payload.get("trigger_type") or "")
    if trigger_type == "cron":
        return CronTrigger(
            minute=_serialized_cron_expression(payload.get("minute", "*")),
            hour=_serialized_cron_expression(payload.get("hour", "*")),
            day=_serialized_cron_expression(payload.get("day", "*")),
            month=_serialized_cron_expression(payload.get("month", "*")),
            day_of_week=_serialized_cron_expression(
                payload.get("day_of_week", "*")
            ),
            timezone=str(payload.get("timezone", "UTC")),
        )
    if trigger_type == "interval":
        start_at = _deserialize_datetime(payload.get("start_at"), "start_at")
        return IntervalTrigger(
            float(payload["interval_seconds"]),
            start_at=start_at,
            timezone=str(payload.get("timezone", "UTC")),
        )
    if trigger_type == "date":
        run_at = _deserialize_datetime(payload.get("run_at"), "run_at")
        if run_at is None:
            raise ValueError("date trigger requires run_at")
        return DateTrigger(
            run_at,
            timezone=str(payload.get("timezone", "UTC")),
        )
    if trigger_type == "fixed_delay":
        start_at = _deserialize_datetime(payload.get("start_at"), "start_at")
        return FixedDelayTrigger(
            float(payload["delay_seconds"]),
            start_at=start_at,
            timezone=str(payload.get("timezone", "UTC")),
        )
    raise ValueError(f"unsupported trigger type: {trigger_type!r}")


def from_dict(payload: Mapping[str, Any]) -> Trigger:
    """Alias for :func:`load_trigger`."""

    return load_trigger(payload)


def _serialized_cron_expression(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item) for item in value)
    return str(value)


def _deserialize_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        require_aware(value, field_name)
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        require_aware(parsed, field_name)
        return parsed
    raise TypeError(f"{field_name} must be an ISO datetime string or datetime")


__all__ = [
    "CronTrigger",
    "DateTrigger",
    "FixedDelayTrigger",
    "IntervalTrigger",
    "Trigger",
    "from_dict",
    "load_trigger",
]

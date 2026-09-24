"""Session-time helpers shared by market data, storage and analysis layers.

Session ids can arrive in several textual shapes for the same instant
(``2026-09-17T09:30:00+08:00`` vs ``2026-09-17T01:30:00+00:00``).  Comparing
those strings directly reports false duplicates and false ordering errors, so
every layer normalises through the helpers below.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

_FALLBACK_SESSION_KEYS = ("session_id", "session", "time", "date")


def parse_session_time(value: Any) -> datetime | None:
    """Parse a session id into a timezone-aware datetime, or ``None``."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        formats = (
            ("%Y%m%d%H%M%S", 14),
            ("%Y%m%d%H%M", 12),
            ("%Y%m%d", 8),
            ("%Y-%m-%d %H:%M:%S", 19),
            ("%Y-%m-%d %H:%M", 16),
        )
        for time_format, expected_length in formats:
            if len(text) != expected_length:
                continue
            try:
                parsed = datetime.strptime(f"{text}+0000", f"{time_format}%z")
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _timestamp(value: datetime) -> float:
    try:
        return value.timestamp()
    except (OverflowError, OSError, ValueError):
        return float("inf")


def session_sort_key(session_id: str) -> tuple[float, str]:
    parsed = parse_session_time(session_id)
    return (_timestamp(parsed), session_id) if parsed is not None else (float("inf"), session_id)


def session_identity(value: Any) -> str:
    """Return a canonical identity so equal instants collapse to one key."""

    text = str(value or "").strip()
    parsed = parse_session_time(text)
    if parsed is None:
        return text
    return parsed.astimezone(UTC).isoformat()


def bar_session_value(bar: Mapping[str, Any]) -> str:
    """Pick the session value from a bar, tolerating legacy key names."""

    for key in _FALLBACK_SESSION_KEYS:
        value = bar.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def dedupe_sorted_bars(
    bars: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return bars in session order with duplicates collapsed by instant.

    The last occurrence wins (fresher data), while the original session id text
    is preserved so stored/displayed timestamps keep their original shape.
    """

    deduped: dict[str, dict[str, Any]] = {}
    for bar in bars:
        if not isinstance(bar, Mapping):
            continue
        session_id = bar_session_value(bar)
        if not session_id:
            continue
        deduped[session_identity(session_id)] = {str(key): value for key, value in bar.items()}
    return sorted(
        deduped.values(),
        key=lambda bar: session_sort_key(bar_session_value(bar)),
    )

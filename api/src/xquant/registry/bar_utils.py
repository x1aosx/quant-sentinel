from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any


def parse_session_time(value: Any) -> datetime | None:
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


def session_sort_key(session_id: str) -> tuple[float, str]:
    parsed = parse_session_time(session_id)
    return (parsed.timestamp(), session_id) if parsed is not None else (float("inf"), session_id)


def dedupe_sorted_bars(
    bars: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return persisted bars in session order with the latest duplicate kept."""
    deduped: dict[str, dict[str, Any]] = {}
    for bar in bars:
        if not isinstance(bar, Mapping):
            continue
        session_id = str(bar.get("session_id") or "").strip()
        if not session_id:
            continue
        deduped[session_id] = {str(key): value for key, value in bar.items()}
    return sorted(
        deduped.values(),
        key=lambda bar: session_sort_key(str(bar["session_id"])),
    )

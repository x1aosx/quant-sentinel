from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, time, timedelta


@dataclass(frozen=True, slots=True)
class TradingSession:
    """One continuous trading session."""

    start: time
    end: time
    name: str = "regular"
    timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("TradingSession start/end must be naive local times")
        if self.start >= self.end:
            raise ValueError("session start must be before end")
        if not self.name.strip():
            raise ValueError("session name cannot be empty")
        if not self.timezone.strip():
            raise ValueError("session timezone cannot be empty")


class TradingCalendar:
    """Weekday-based mainland China market calendar."""

    SUPPORTED_MARKETS = frozenset({"SSE", "SZSE", "BSE"})
    _DEFAULT_SESSIONS: Mapping[str, tuple[TradingSession, ...]] = {
        "SSE": (
            TradingSession(time(9, 30), time(11, 30), "morning"),
            TradingSession(time(13, 0), time(15, 0), "afternoon"),
        ),
        "SZSE": (
            TradingSession(time(9, 30), time(11, 30), "morning"),
            TradingSession(time(13, 0), time(15, 0), "afternoon"),
        ),
        "BSE": (
            TradingSession(time(9, 30), time(11, 30), "morning"),
            TradingSession(time(13, 0), time(15, 0), "afternoon"),
        ),
    }

    def __init__(
        self,
        holidays: Mapping[str, Iterable[date]] | None = None,
    ) -> None:
        self._holidays = {
            self._normalize_market(market): frozenset(days)
            for market, days in (holidays or {}).items()
        }

    @classmethod
    def _normalize_market(cls, market: str) -> str:
        normalized = market.strip().upper()
        if normalized not in cls.SUPPORTED_MARKETS:
            supported = ", ".join(sorted(cls.SUPPORTED_MARKETS))
            raise ValueError(f"unsupported market {market!r}; expected one of {supported}")
        return normalized

    def is_trading_day(self, market: str, current_date: date) -> bool:
        normalized = self._normalize_market(market)
        return (
            current_date.weekday() < 5
            and current_date not in self._holidays.get(normalized, frozenset())
        )

    def get_sessions(self, market: str, current_date: date) -> list[TradingSession]:
        normalized = self._normalize_market(market)
        if not self.is_trading_day(normalized, current_date):
            return []
        return list(self._DEFAULT_SESSIONS[normalized])

    def next_trading_day(self, market: str, current_date: date) -> date:
        normalized = self._normalize_market(market)
        candidate = current_date + timedelta(days=1)
        while not self.is_trading_day(normalized, candidate):
            candidate += timedelta(days=1)
        return candidate

    def previous_trading_day(self, market: str, current_date: date) -> date:
        normalized = self._normalize_market(market)
        candidate = current_date - timedelta(days=1)
        while not self.is_trading_day(normalized, candidate):
            candidate -= timedelta(days=1)
        return candidate


__all__ = ["TradingCalendar", "TradingSession"]

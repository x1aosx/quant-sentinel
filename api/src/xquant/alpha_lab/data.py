from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np

from .errors import InsufficientDataError


def _float_array(values: Sequence[Any]) -> np.ndarray:
    return np.asarray([float(value) for value in values], dtype=np.float64)


@dataclass(frozen=True)
class BarFrame:
    symbol: str
    timeframe: str
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    time: np.ndarray
    is_closed: np.ndarray
    source: str = "unknown"
    adjustment: str = "none"

    def __post_init__(self) -> None:
        arrays = (self.open, self.high, self.low, self.close, self.volume, self.time)
        if any(array.ndim != 2 for array in arrays):
            raise ValueError("bar arrays must have shape [N, T]")
        if any(array.shape != self.open.shape for array in arrays):
            raise ValueError("bar arrays must share the same shape")
        if self.open.size == 0:
            raise ValueError("bar frame cannot be empty")
        if not all(
            np.isfinite(array).all()
            for array in (self.open, self.high, self.low, self.close, self.volume)
        ):
            raise ValueError("bar prices and volume must be finite")
        if np.any(self.open <= 0) or np.any(self.high <= 0) or np.any(self.low <= 0):
            raise ValueError("bar prices must be positive")
        if np.any(self.high < np.maximum(self.open, self.close)):
            raise ValueError("high must be greater than or equal to open/close")
        if np.any(self.low > np.minimum(self.open, self.close)):
            raise ValueError("low must be less than or equal to open/close")
        if np.any(self.volume < 0):
            raise ValueError("volume cannot be negative")
        if self.is_closed.shape != self.open.shape:
            raise ValueError("is_closed must have shape [N, T]")

    @property
    def n_symbols(self) -> int:
        return int(self.open.shape[0])

    @property
    def n_bars(self) -> int:
        return int(self.open.shape[1])

    @classmethod
    def from_dataset(
        cls,
        dataset: Mapping[str, Any],
        *,
        adjustment: str = "none",
    ) -> BarFrame:
        summary = dataset.get("summary")
        summary = summary if isinstance(summary, Mapping) else dataset
        bars = dataset.get("bars") or []
        if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)):
            raise TypeError("dataset bars must be a sequence")
        if not bars:
            raise InsufficientDataError("dataset has no bars")
        sessions = [str(bar.get("session_id") or "") for bar in bars]
        timestamps = np.arange(len(sessions), dtype=np.float64)[None, :]
        closed = np.ones((1, len(bars)), dtype=bool)
        symbol = str(summary.get("symbol") or dataset.get("symbol") or "UNKNOWN")
        timeframe = str(summary.get("timeframe") or dataset.get("timeframe") or "1d")
        source = str(summary.get("source") or dataset.get("source") or "unknown")
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            open=_float_array([bar.get("open") for bar in bars])[None, :],
            high=_float_array([bar.get("high") for bar in bars])[None, :],
            low=_float_array([bar.get("low") for bar in bars])[None, :],
            close=_float_array([bar.get("close") for bar in bars])[None, :],
            volume=_float_array([bar.get("volume", 0.0) for bar in bars])[None, :],
            time=timestamps,
            is_closed=closed,
            source=source,
            adjustment=adjustment,
        )

    @classmethod
    def from_bars(
        cls,
        bars: Sequence[Mapping[str, Any]],
        *,
        symbol: str = "UNKNOWN",
        timeframe: str = "1d",
        source: str = "local",
        adjustment: str = "none",
        closed_only: bool = True,
    ) -> BarFrame:
        filtered = [
            bar
            for bar in bars
            if not closed_only or bool(bar.get("is_closed", bar.get("closed", True)))
        ]
        if not filtered:
            raise InsufficientDataError("no closed bars are available")
        return cls.from_dataset(
            {
                "summary": {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source": source,
                },
                "bars": filtered,
            },
            adjustment=adjustment,
        )

    def last_closed(self) -> BarFrame:
        if not bool(self.is_closed[0, -1]):
            return self.slice_time(0, self.n_bars - 1)
        return self

    def slice_time(self, start: int, end: int | None = None) -> BarFrame:
        stop = self.n_bars if end is None else end
        if start < 0 or stop <= start or stop > self.n_bars:
            raise ValueError("invalid bar frame slice")
        return BarFrame(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open=self.open[:, start:stop],
            high=self.high[:, start:stop],
            low=self.low[:, start:stop],
            close=self.close[:, start:stop],
            volume=self.volume[:, start:stop],
            time=self.time[:, start:stop],
            is_closed=self.is_closed[:, start:stop],
            source=self.source,
            adjustment=self.adjustment,
        )

    def content_hash(self) -> str:
        digest = hashlib.sha256()
        for array in (
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.time,
        ):
            digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()


class MarketDataPort(Protocol):
    def load_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
    ) -> BarFrame:
        ...


class XQSMarketDataAdapter:
    """Adapt the existing XQS dataset facade without creating a second data center."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def load_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        closed_only: bool = True,
        dataset_id: str | None = None,
        adjustment: str = "none",
    ) -> BarFrame:
        if dataset_id:
            dataset = self.database.get_dataset(dataset_id)
        else:
            normalized_symbol = str(symbol or "").strip().upper()
            normalized_timeframe = str(timeframe or "1d").strip().lower() or "1d"
            match = next(
                (
                    item
                    for item in self.database.list_datasets()
                    if str(item.get("symbol") or "").strip().upper() == normalized_symbol
                    and str(item.get("timeframe") or "").strip().lower()
                    == normalized_timeframe
                ),
                None,
            )
            if match is None:
                raise KeyError(
                    f"dataset not found for {normalized_symbol} {normalized_timeframe}"
                )
            dataset = self.database.get_dataset(str(match["id"]))
        frame = BarFrame.from_dataset(dataset, adjustment=adjustment)
        if closed_only and not bool(frame.is_closed[0, -1]):
            frame = frame.slice_time(0, frame.n_bars - 1)
        if start is not None or end is not None:
            frame = _filter_time(frame, start, end)
        if limit is not None:
            frame = frame.slice_time(max(0, frame.n_bars - int(limit)), frame.n_bars)
        return frame


@dataclass(frozen=True)
class DataSnapshot:
    snapshot_id: str
    market: str
    symbols: tuple[str, ...]
    timeframe: str
    start_at: str
    end_at: str
    adjustment: str
    source: str
    row_count: int
    content_hash: str
    schema_version: str = "bars-v1"
    created_at: str = ""
    storage_uri: str = ""

    @classmethod
    def from_frame(
        cls,
        frame: BarFrame,
        *,
        market: str = "CN-A",
        storage_uri: str = "",
    ) -> DataSnapshot:
        content_hash = frame.content_hash()
        now = datetime.now(UTC).isoformat()
        snapshot_id = hashlib.sha256(
            f"{content_hash}:{frame.symbol}:{frame.timeframe}:{now}".encode()
        ).hexdigest()[:24]
        return cls(
            snapshot_id=snapshot_id,
            market=market,
            symbols=(frame.symbol,),
            timeframe=frame.timeframe,
            start_at=str(frame.time[0, 0]),
            end_at=str(frame.time[0, -1]),
            adjustment=frame.adjustment,
            source=frame.source,
            row_count=frame.n_bars,
            content_hash=content_hash,
            created_at=now,
            storage_uri=storage_uri,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["symbols"] = list(self.symbols)
        return payload


def _filter_time(
    frame: BarFrame,
    start: datetime | None,
    end: datetime | None,
) -> BarFrame:
    if start is None and end is None:
        return frame
    # XQS session ids are strings, so a strict datetime filter is only possible
    # after a caller has supplied timestamped bars. Keep the adapter conservative.
    if start is not None and start > datetime.now(UTC):
        raise ValueError("start cannot be in the future")
    if end is not None and start is not None and end < start:
        raise ValueError("end must be after start")
    return frame

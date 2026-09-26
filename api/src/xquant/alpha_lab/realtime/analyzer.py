from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np

from ..config import AlphaLabSettings
from ..data import BarFrame, MarketDataPort
from ..domain import SignalDecision, SignalState, StrategyArtifact
from ..errors import InsufficientDataError
from ..factor import FEATURE_REGISTRY, FactorRuntime, SignalKernel
from ..strategy import StrategyRepository, ensure_artifact_hash, validate_artifact_schema
from .models import DirectionFlipEvent, RealtimeWatch, SignalRecord
from .stores import (
    InMemorySignalStore,
    RealtimeWatchRepository,
    SignalStore,
)


class EventPublisher(Protocol):
    def publish(self, event: DirectionFlipEvent) -> None: ...


def bar_idempotency_key(source: str, symbol: str, timeframe: str, bar_ts: str) -> str:
    value = f"{source}\x1f{symbol}\x1f{timeframe}\x1f{bar_ts}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def signal_idempotency_key(
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    timeframe: str,
    bar_ts: str,
) -> str:
    value = (
        f"{strategy_id}\x1f{strategy_version}\x1f"
        f"{symbol}\x1f{timeframe}\x1f{bar_ts}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def watch_idempotency_key(
    source: str,
    symbol: str,
    timeframe: str,
    strategy_id: str,
    strategy_version: str,
) -> str:
    return (
        f"{source}|{symbol}|{timeframe}|"
        f"{strategy_id}|{strategy_version}"
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _closed_frame(frame: BarFrame) -> BarFrame:
    mask = np.all(frame.is_closed, axis=0)
    if bool(mask.all()):
        return frame
    if not bool(mask.any()):
        raise InsufficientDataError("no closed bars are available")
    return BarFrame(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        open=frame.open[:, mask],
        high=frame.high[:, mask],
        low=frame.low[:, mask],
        close=frame.close[:, mask],
        volume=frame.volume[:, mask],
        time=frame.time[:, mask],
        is_closed=frame.is_closed[:, mask],
        source=frame.source,
        adjustment=frame.adjustment,
        session=None if frame.session is None else frame.session[:, mask],
    )


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float, np.integer, np.floating)):
        numeric = float(value)
        if not np.isfinite(numeric):
            return None
        return numeric / 1_000.0 if numeric > 10_000_000_000 else numeric
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _now_seconds(value: Any) -> float:
    if value is None:
        return datetime.now(UTC).timestamp()
    parsed = _timestamp_seconds(value)
    if parsed is None:
        raise ValueError(f"invalid current time: {value!r}")
    return parsed


class RealtimeAnalyzer:
    """Run the shared factor/signal runtime against the latest closed bar."""

    def __init__(
        self,
        *,
        strategy_repository: StrategyRepository,
        watch_repository: RealtimeWatchRepository,
        signal_store: SignalStore | None = None,
        event_publisher: EventPublisher | None = None,
        settings: AlphaLabSettings | None = None,
        market_data: MarketDataPort | None = None,
        factor_runtime: FactorRuntime | None = None,
        signal_kernel: SignalKernel | None = None,
        min_bars: int | None = None,
        stale_after_seconds: float | None = None,
        now_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings if settings is not None else AlphaLabSettings()
        self.strategy_repository = strategy_repository
        self.watch_repository = watch_repository
        self.signal_store = signal_store if signal_store is not None else InMemorySignalStore()
        self.event_publisher = event_publisher
        self.market_data = market_data
        self.factor_runtime = (
            factor_runtime if factor_runtime is not None else FactorRuntime()
        )
        self.signal_kernel = (
            signal_kernel
            if signal_kernel is not None
            else SignalKernel(self.settings.mining)
        )
        self.min_bars = (
            int(min_bars)
            if min_bars is not None
            else int(self.settings.mining.realtime_min_bars)
        )
        if self.min_bars <= 0:
            raise ValueError("min_bars must be positive")
        self.stale_after_seconds = stale_after_seconds
        self.now_provider = now_provider or (lambda: datetime.now(UTC).timestamp())

    @property
    def required_history(self) -> int:
        feature_lookback = max(
            (spec.lookback for spec in FEATURE_REGISTRY.specs),
            default=1,
        )
        return max(self.min_bars, feature_lookback + 1)

    def analyze_frame(
        self,
        artifact: StrategyArtifact,
        frame: BarFrame,
        *,
        watch: RealtimeWatch | None = None,
        source: str | None = None,
        now: Any = None,
    ) -> SignalRecord:
        artifact = ensure_artifact_hash(artifact)
        validate_artifact_schema(artifact)
        source_name = str(source or (watch.source if watch else frame.source))
        current_time = _now_seconds(self.now_provider() if now is None else now)

        try:
            frame = _closed_frame(frame)
        except InsufficientDataError as exc:
            return self._state_record(
                artifact,
                frame,
                source=source_name,
                watch=watch,
                state=SignalState.PENDING,
                message=str(exc),
            )

        bar_ts = str(frame.time[0, -1])
        expected_watch_id = self._watch_id(source_name, frame.symbol, frame.timeframe, artifact)
        current_watch = watch or self.watch_repository.get(expected_watch_id)
        if current_watch is None:
            current_watch = RealtimeWatch(
                id=expected_watch_id,
                source=source_name,
                symbol=frame.symbol,
                timeframe=frame.timeframe,
                strategy_id=artifact.strategy_id,
                strategy_version=artifact.version,
            )

        signal_key = signal_idempotency_key(
            artifact.strategy_id,
            artifact.version,
            frame.symbol,
            frame.timeframe,
            bar_ts,
        )
        existing_signal = self.signal_store.get(signal_key)
        if existing_signal is not None:
            return replace(existing_signal, idempotent=True)
        if current_watch.last_closed_bar_ts == bar_ts:
            record = self._record_from_watch(current_watch, signal_key)
            self.signal_store.save_if_absent(record)
            return replace(record, idempotent=True)

        if self.stale_after_seconds is not None:
            bar_time = _timestamp_seconds(frame.time[0, -1])
            if bar_time is None or current_time - bar_time > self.stale_after_seconds:
                return self._state_record(
                    artifact,
                    frame,
                    source=source_name,
                    watch=current_watch,
                    state=SignalState.ERROR,
                    message="stale market data: latest closed bar exceeds threshold",
                    bar_ts=bar_ts,
                )

        required_bars = self.required_history
        if frame.n_bars < required_bars:
            return self._state_record(
                artifact,
                frame,
                source=source_name,
                watch=current_watch,
                state=SignalState.INSUFFICIENT,
                message=f"insufficient closed bars: {frame.n_bars}/{required_bars}",
                bar_ts=bar_ts,
            )

        features = FEATURE_REGISTRY.compute(frame)
        factors = self.factor_runtime.evaluate(
            artifact.formula_tokens,
            features,
            artifact.factor_schema_version,
        )
        decision = self.signal_kernel.evaluate_last(
            factors=factors,
            strategy_id=artifact.strategy_id,
            strategy_version=artifact.version,
            formula_tokens=artifact.formula_tokens,
            factor_schema_version=artifact.factor_schema_version,
            bar_time=bar_ts,
            data_snapshot_id=artifact.data_snapshot_id,
        )
        record = self._record_from_decision(
            decision,
            signal_key=signal_key,
            bar_key=bar_idempotency_key(
                source_name,
                frame.symbol,
                frame.timeframe,
                bar_ts,
            ),
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            source=source_name,
        )
        stored, created = self.signal_store.save_if_absent(record)
        if not created:
            return replace(stored, idempotent=True)

        previous_direction = current_watch.last_direction
        updated_watch = replace(
            current_watch,
            state=SignalState.OK,
            last_closed_bar_ts=bar_ts,
            last_factor=decision.factor_value,
            last_position=decision.position,
            last_direction=decision.direction,
            last_strength=decision.strength,
            updated_at=_utc_now(),
            error="",
        )
        self.watch_repository.save(updated_watch)
        if (
            current_watch.last_closed_bar_ts
            and decision.direction is not previous_direction
            and self.event_publisher is not None
        ):
            self.event_publisher.publish(
                DirectionFlipEvent(
                    symbol=frame.symbol,
                    timeframe=frame.timeframe,
                    strategy_id=artifact.strategy_id,
                    strategy_version=artifact.version,
                    previous_direction=previous_direction,
                    direction=decision.direction,
                    position=decision.position,
                    strength=decision.strength,
                    factor_value=decision.factor_value,
                    bar_close_ts=bar_ts,
                    signal_key=signal_key,
                )
            )
        return stored

    def analyze(
        self,
        strategy_id: str,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        source: str = "local",
        frame: BarFrame | None = None,
        version: str | None = None,
        watch_id: str | None = None,
        now: Any = None,
        limit: int | None = None,
    ) -> SignalRecord:
        artifact = self.strategy_repository.get(strategy_id, version)
        resolved_symbol = symbol or artifact.symbol
        resolved_timeframe = timeframe or artifact.timeframe
        if frame is None:
            if self.market_data is None:
                raise ValueError("frame or market_data is required")
            frame = self.market_data.load_bars(
                resolved_symbol,
                resolved_timeframe,
                limit=limit or self.required_history,
                closed_only=True,
            )
        resolved_watch_id = watch_id or self._watch_id(
            source,
            resolved_symbol,
            resolved_timeframe,
            artifact,
        )
        watch = self.watch_repository.get(resolved_watch_id)
        return self.analyze_frame(
            artifact,
            frame,
            watch=watch,
            source=source,
            now=now,
        )

    @staticmethod
    def _watch_id(
        source: str,
        symbol: str,
        timeframe: str,
        artifact: StrategyArtifact,
    ) -> str:
        return watch_idempotency_key(
            source,
            symbol,
            timeframe,
            artifact.strategy_id,
            artifact.version,
        )

    @staticmethod
    def _record_from_decision(
        decision: SignalDecision,
        *,
        signal_key: str,
        bar_key: str,
        symbol: str,
        timeframe: str,
        source: str,
    ) -> SignalRecord:
        return SignalRecord(
            signal_key=signal_key,
            bar_key=bar_key,
            strategy_id=decision.strategy_id,
            strategy_version=decision.strategy_version,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            bar_close_ts=decision.bar_time,
            direction=decision.direction,
            position=decision.position,
            strength=decision.strength,
            factor_value=decision.factor_value,
            state=decision.state,
            bars_used=decision.bars_used,
            generated_at=decision.generated_at,
            message=decision.message,
        )

    @staticmethod
    def _record_from_watch(watch: RealtimeWatch, signal_key: str) -> SignalRecord:
        return SignalRecord(
            signal_key=signal_key,
            bar_key=bar_idempotency_key(
                watch.source,
                watch.symbol,
                watch.timeframe,
                watch.last_closed_bar_ts,
            ),
            strategy_id=watch.strategy_id,
            strategy_version=watch.strategy_version,
            symbol=watch.symbol,
            timeframe=watch.timeframe,
            source=watch.source,
            bar_close_ts=watch.last_closed_bar_ts,
            direction=watch.last_direction,
            position=float(watch.last_position or 0.0),
            strength=float(watch.last_strength or 0.0),
            factor_value=float(watch.last_factor or 0.0),
            state=watch.state,
            bars_used=0,
            message=watch.error,
            idempotent=True,
        )

    def _state_record(
        self,
        artifact: StrategyArtifact,
        frame: BarFrame,
        *,
        source: str,
        watch: RealtimeWatch | None,
        state: SignalState,
        message: str,
        bar_ts: str = "",
    ) -> SignalRecord:
        resolved_bar_ts = str(bar_ts or frame.time[0, -1])
        current_watch = watch or RealtimeWatch(
            id=self._watch_id(source, frame.symbol, frame.timeframe, artifact),
            source=source,
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            strategy_id=artifact.strategy_id,
            strategy_version=artifact.version,
        )
        updated_watch = replace(
            current_watch,
            state=state,
            updated_at=_utc_now(),
            error=message,
        )
        self.watch_repository.save(updated_watch)
        return SignalRecord(
            signal_key=signal_idempotency_key(
                artifact.strategy_id,
                artifact.version,
                frame.symbol,
                frame.timeframe,
                resolved_bar_ts,
            ),
            bar_key=bar_idempotency_key(
                source,
                frame.symbol,
                frame.timeframe,
                resolved_bar_ts,
            ),
            strategy_id=artifact.strategy_id,
            strategy_version=artifact.version,
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            source=source,
            bar_close_ts=resolved_bar_ts,
            direction=current_watch.last_direction,
            position=float(current_watch.last_position or 0.0),
            strength=float(current_watch.last_strength or 0.0),
            factor_value=float(current_watch.last_factor or 0.0),
            state=state,
            bars_used=frame.n_bars,
            message=message,
        )

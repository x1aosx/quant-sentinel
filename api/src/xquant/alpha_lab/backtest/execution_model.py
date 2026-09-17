from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AShareExecutionConfig:
    """Execution semantics for the default China A-share backtest profile."""

    execution_lag_bars: int = 1
    t_plus_one: bool = True
    allow_short: bool = False
    max_long_exposure: float = 1.0
    max_short_exposure: float = 1.0

    def __post_init__(self) -> None:
        if self.execution_lag_bars < 1:
            raise ValueError("execution_lag_bars must be at least 1 to avoid same-bar leakage")
        if self.max_long_exposure < 0 or self.max_short_exposure < 0:
            raise ValueError("maximum exposure cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_lag_bars": self.execution_lag_bars,
            "t_plus_one": self.t_plus_one,
            "allow_short": self.allow_short,
            "max_long_exposure": self.max_long_exposure,
            "max_short_exposure": self.max_short_exposure,
        }


@dataclass(frozen=True)
class ExecutionDecision:
    raw_target: float
    target_position: float
    current_position: float
    short_blocked: bool = False
    t_plus_one_blocked: bool = False
    reason: str = "executed"

    @property
    def changed(self) -> bool:
        return not np.isclose(self.target_position, self.current_position, atol=1e-12)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_target": self.raw_target,
            "target_position": self.target_position,
            "current_position": self.current_position,
            "short_blocked": self.short_blocked,
            "t_plus_one_blocked": self.t_plus_one_blocked,
            "reason": self.reason,
        }


class AShareExecutionModel:
    """Turn close-derived signals into executable positions at later bar opens."""

    name = "a_share_execution"

    def __init__(
        self,
        config: AShareExecutionConfig | None = None,
        *,
        execution_lag_bars: int | None = None,
        t_plus_one: bool | None = None,
        allow_short: bool | None = None,
        max_long_exposure: float | None = None,
        max_short_exposure: float | None = None,
    ) -> None:
        base = config or AShareExecutionConfig()
        self.config = AShareExecutionConfig(
            execution_lag_bars=(
                base.execution_lag_bars
                if execution_lag_bars is None
                else execution_lag_bars
            ),
            t_plus_one=base.t_plus_one if t_plus_one is None else t_plus_one,
            allow_short=base.allow_short if allow_short is None else allow_short,
            max_long_exposure=(
                base.max_long_exposure
                if max_long_exposure is None
                else max_long_exposure
            ),
            max_short_exposure=(
                base.max_short_exposure
                if max_short_exposure is None
                else max_short_exposure
            ),
        )

    @property
    def execution_lag_bars(self) -> int:
        return self.config.execution_lag_bars

    @property
    def t_plus_one(self) -> bool:
        return self.config.t_plus_one

    @property
    def allow_short(self) -> bool:
        return self.config.allow_short

    def schedule(self, signal_positions: np.ndarray) -> np.ndarray:
        """Shift close-derived targets so bar t is executed at a later open."""

        signals = np.asarray(signal_positions, dtype=np.float64)
        if signals.ndim != 2:
            raise ValueError("signal_positions must have shape [N, T]")
        if not np.isfinite(signals).all():
            raise ValueError("signal_positions must be finite")
        scheduled = np.zeros_like(signals)
        lag = self.execution_lag_bars
        if lag < signals.shape[1]:
            scheduled[:, lag:] = signals[:, :-lag]
        return scheduled

    def resolve(
        self,
        *,
        raw_target: float,
        current_position: float,
        bar_index: int,
        entry_session: int | str | None = None,
        session: int | str | None = None,
    ) -> ExecutionDecision:
        """Apply the A-share short-sale ban and T+1 lock to a target position."""

        target = float(raw_target)
        current = float(current_position)
        if not np.isfinite(target) or not np.isfinite(current):
            raise ValueError("positions must be finite")

        short_blocked = False
        if target < 0:
            if self.allow_short:
                target = max(target, -self.config.max_short_exposure)
            else:
                target = 0.0
                short_blocked = True
        target = min(target, self.config.max_long_exposure)

        t_plus_one_blocked = False
        same_session = (
            entry_session is not None
            and session is not None
            and entry_session == session
        )
        if (
            self.t_plus_one
            and current > 0.0
            and target < current
            and same_session
        ):
            target = current
            t_plus_one_blocked = True

        reason = "executed"
        if short_blocked and t_plus_one_blocked:
            reason = "short_blocked_and_t_plus_one"
        elif short_blocked:
            reason = "short_blocked"
        elif t_plus_one_blocked:
            reason = "t_plus_one_blocked"

        return ExecutionDecision(
            raw_target=float(raw_target),
            target_position=float(target),
            current_position=current,
            short_blocked=short_blocked,
            t_plus_one_blocked=t_plus_one_blocked,
            reason=reason,
        )

    @staticmethod
    def session_keys(time: np.ndarray) -> np.ndarray:
        """Create coarse trading-session keys from epoch timestamps.

        Numeric time arrays that are not Unix-like timestamps fall back to one
        session per bar. This keeps index-based test fixtures deterministic.
        """

        values = np.asarray(time, dtype=np.float64).reshape(-1)
        if values.size == 0:
            return np.asarray([], dtype=object)
        absolute = np.abs(values)
        epoch_scale = float(np.median(absolute))
        if epoch_scale >= 1e17:
            seconds = values / 1e9
        elif epoch_scale >= 1e14:
            seconds = values / 1e6
        elif epoch_scale >= 1e11:
            seconds = values / 1e3
        elif epoch_scale >= 1e9:
            seconds = values
        else:
            return np.asarray(list(range(values.size)), dtype=object)

        keys: list[int | str] = []
        for value in seconds:
            try:
                keys.append(datetime.fromtimestamp(float(value), tz=UTC).date().isoformat())
            except (OverflowError, OSError, ValueError):
                keys.append(str(float(value)))
        return np.asarray(keys, dtype=object)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.config.to_dict()}


ExecutionConfig = AShareExecutionConfig
ExecutionModel = AShareExecutionModel
T1ExecutionModel = AShareExecutionModel

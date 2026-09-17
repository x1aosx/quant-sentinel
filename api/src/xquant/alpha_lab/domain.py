from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class SignalState(StrEnum):
    OK = "ok"
    INSUFFICIENT = "insufficient"
    PENDING = "pending"
    ERROR = "error"


class TrainingStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StrategyStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PRODUCTION = "production"
    RETIRED = "retired"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SignalDecision:
    strategy_id: str
    strategy_version: str
    formula_tokens: tuple[int, ...]
    factor_schema_version: str
    direction: Direction
    position: float
    strength: float
    factor_value: float
    bars_used: int
    bar_time: str
    state: SignalState = SignalState.OK
    data_snapshot_id: str = ""
    message: str = ""
    generated_at: str = field(default_factory=_utc_now)

    @property
    def is_tradeable(self) -> bool:
        return self.state is SignalState.OK and self.direction is not Direction.FLAT

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["state"] = self.state.value
        payload["formula_tokens"] = list(self.formula_tokens)
        payload["is_tradeable"] = self.is_tradeable
        return payload


@dataclass(frozen=True)
class TrainingRun:
    id: str
    dataset_id: str
    symbol: str
    timeframe: str
    status: TrainingStatus = TrainingStatus.QUEUED
    progress: float = 0.0
    step: int = 0
    best_formula_tokens: tuple[int, ...] = ()
    best_score: float | None = None
    error: str = ""
    checkpoint_uri: str = ""
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["best_formula_tokens"] = list(self.best_formula_tokens)
        return payload


@dataclass(frozen=True)
class StrategyArtifact:
    strategy_id: str
    version: str
    name: str
    symbol: str
    timeframe: str
    formula_tokens: tuple[int, ...]
    factor_schema_version: str
    signal_kernel: str = "tanh_continuous_v1"
    min_exposure: float = 0.05
    status: StrategyStatus = StrategyStatus.CANDIDATE
    content_hash: str = ""
    data_snapshot_id: str = ""
    training_run_id: str = ""
    created_at: str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["formula_tokens"] = list(self.formula_tokens)
        payload["status"] = self.status.value
        payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    strategy_id: str
    strategy_version: str
    dataset_id: str
    initial_equity: float
    final_equity: float
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    turnover: float
    trade_count: int
    equity_curve: tuple[float, ...] = ()
    trade_log: tuple[Mapping[str, Any], ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["equity_curve"] = list(self.equity_curve)
        payload["trade_log"] = [dict(item) for item in self.trade_log]
        payload["metrics"] = dict(self.metrics)
        return payload

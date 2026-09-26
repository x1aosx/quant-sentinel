from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..domain import BacktestResult
from .costs import CostBreakdown


def _mapping_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True)
class ExecutionRecord:
    symbol: str
    signal_bar: int
    execution_bar: int
    execution_time: float
    from_position: float
    to_position: float
    raw_target: float
    price: float
    equity_before: float
    equity_after_cost: float
    turnover: float
    cost: CostBreakdown
    short_blocked: bool = False
    t_plus_one_blocked: bool = False
    reason: str = "executed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "signal_bar": self.signal_bar,
            "execution_bar": self.execution_bar,
            "execution_time": self.execution_time,
            "from_position": self.from_position,
            "to_position": self.to_position,
            "raw_target": self.raw_target,
            "price": self.price,
            "equity_before": self.equity_before,
            "equity_after_cost": self.equity_after_cost,
            "turnover": self.turnover,
            "cost": self.cost.to_dict(),
            "short_blocked": self.short_blocked,
            "t_plus_one_blocked": self.t_plus_one_blocked,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    direction: str
    entry_bar: int
    exit_bar: int
    entry_time: float
    exit_time: float
    entry_price: float
    exit_price: float
    holding_bars: int
    pnl: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SymbolBacktestResult:
    symbol: str
    metrics: Mapping[str, Any]
    equity_curve: tuple[float, ...]
    drawdown_curve: tuple[float, ...]
    target_positions: tuple[float, ...]
    actual_positions: tuple[float, ...]
    total_cost: float
    cost_breakdown: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "metrics": _mapping_copy(self.metrics),
            "equity_curve": list(self.equity_curve),
            "drawdown_curve": list(self.drawdown_curve),
            "target_positions": list(self.target_positions),
            "actual_positions": list(self.actual_positions),
            "total_cost": self.total_cost,
            "cost_breakdown": _mapping_copy(self.cost_breakdown),
        }


@dataclass(frozen=True)
class BacktestReport:
    run_id: str
    formula_tokens: tuple[int, ...]
    factor_schema_version: str
    strategy_id: str
    strategy_version: str
    dataset_id: str
    initial_equity: float
    final_equity: float
    metrics: Mapping[str, Any]
    equity_curve: tuple[float, ...]
    drawdown_curve: tuple[float, ...]
    trade_log: tuple[TradeRecord, ...]
    executions: tuple[ExecutionRecord, ...]
    per_symbol: tuple[SymbolBacktestResult, ...]
    cost_breakdown: Mapping[str, float]
    execution_model: Mapping[str, Any]
    cost_model: Mapping[str, Any]
    robustness: Mapping[str, Any] = field(default_factory=dict)
    # ``time_axis`` labels every point of ``equity_curve``. ``time_axis_kind``
    # records whether those labels are real exchange sessions or the synthetic
    # bar index, so a consumer never has to invent dates.
    time_axis: tuple[str, ...] = ()
    time_axis_kind: str = "bar_index"
    rolling_sharpe: tuple[float, ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def total_return(self) -> float:
        return float(self.metrics.get("total_return", 0.0))

    @property
    def annualized_return(self) -> float:
        return float(self.metrics.get("annualized_return", 0.0))

    @property
    def max_drawdown(self) -> float:
        return float(self.metrics.get("max_drawdown", 0.0))

    @property
    def sharpe(self) -> float:
        return float(self.metrics.get("sharpe", 0.0))

    @property
    def turnover(self) -> float:
        return float(self.metrics.get("turnover", 0.0))

    @property
    def trade_count(self) -> int:
        return int(self.metrics.get("trade_count", 0))

    @property
    def actual_positions(self) -> tuple[float, ...]:
        if not self.per_symbol:
            return ()
        positions = np.mean(
            np.stack(
                [
                    np.asarray(item.actual_positions, dtype=np.float64)
                    for item in self.per_symbol
                ],
                axis=0,
            ),
            axis=0,
        )
        return tuple(float(value) for value in positions)

    def axis_label(self, index: int) -> str:
        """Return the x-axis label for a bar index (falls back to the index)."""

        if 0 <= index < len(self.time_axis):
            return str(self.time_axis[index])
        return str(index)

    def _trade_public(self, trade: TradeRecord) -> dict[str, Any]:
        payload = trade.to_dict()
        payload["side"] = trade.direction
        payload["return_pct"] = float(trade.pnl)
        payload["entry_session"] = self.axis_label(trade.entry_bar)
        payload["exit_session"] = self.axis_label(trade.exit_bar)
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "dataset_id": self.dataset_id,
            "formula_tokens": list(self.formula_tokens),
            "factor_schema_version": self.factor_schema_version,
            "initial_equity": self.initial_equity,
            "final_equity": self.final_equity,
            "metrics": _mapping_copy(self.metrics),
            "equity_curve": list(self.equity_curve),
            "drawdown_curve": list(self.drawdown_curve),
            "time_axis": list(self.time_axis),
            "time_axis_kind": self.time_axis_kind,
            "rolling_sharpe": list(self.rolling_sharpe),
            "trade_log": [self._trade_public(trade) for trade in self.trade_log],
            "executions": [execution.to_dict() for execution in self.executions],
            "per_symbol": [item.to_dict() for item in self.per_symbol],
            "cost_breakdown": _mapping_copy(self.cost_breakdown),
            "execution_model": _mapping_copy(self.execution_model),
            "cost_model": _mapping_copy(self.cost_model),
            "robustness": _mapping_copy(self.robustness),
            "created_at": self.created_at,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def write_json(self, path: str | Path, *, indent: int | None = 2) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.to_json(indent=indent), encoding="utf-8")
        return destination

    def to_domain_result(self) -> BacktestResult:
        return BacktestResult(
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            dataset_id=self.dataset_id,
            initial_equity=self.initial_equity,
            final_equity=self.final_equity,
            total_return=float(self.metrics.get("total_return", 0.0)),
            annualized_return=float(self.metrics.get("annualized_return", 0.0)),
            max_drawdown=float(self.metrics.get("max_drawdown", 0.0)),
            sharpe=float(self.metrics.get("sharpe", 0.0)),
            turnover=float(self.metrics.get("turnover", 0.0)),
            trade_count=int(self.metrics.get("trade_count", 0)),
            equity_curve=self.equity_curve,
            trade_log=tuple(trade.to_dict() for trade in self.trade_log),
            metrics=_mapping_copy(self.metrics),
            created_at=self.created_at,
        )


def aggregate_cost_breakdown(
    items: list[CostBreakdown],
) -> dict[str, float]:
    keys = (
        "turnover",
        "turnover_notional",
        "buy_turnover",
        "sell_turnover",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "slippage",
        "total",
    )
    return {
        key: float(sum(float(getattr(item, key)) for item in items))
        for key in keys
    }

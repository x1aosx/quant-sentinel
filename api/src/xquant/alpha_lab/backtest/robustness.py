from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from ..data import BarFrame
from .metrics import calculate_metrics
from .report import BacktestReport

if TYPE_CHECKING:
    from .engine import BacktestEngine


@dataclass(frozen=True)
class RobustnessAuditResult:
    status: str
    issues: tuple[str, ...]
    subperiods: tuple[dict[str, Any], ...]
    cost_stress: tuple[dict[str, Any], ...]
    per_symbol: tuple[dict[str, Any], ...]
    monthly_returns: dict[str, float]
    annual_returns: dict[str, float]
    holding_period: dict[str, float]
    turnover_audit: dict[str, float]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = list(self.issues)
        payload["subperiods"] = [dict(item) for item in self.subperiods]
        payload["cost_stress"] = [dict(item) for item in self.cost_stress]
        payload["per_symbol"] = [dict(item) for item in self.per_symbol]
        payload["monthly_returns"] = dict(self.monthly_returns)
        payload["annual_returns"] = dict(self.annual_returns)
        payload["holding_period"] = dict(self.holding_period)
        payload["turnover_audit"] = dict(self.turnover_audit)
        payload["metadata"] = dict(self.metadata)
        return payload


class RobustnessAuditor:
    """Run deterministic robustness checks against an existing BacktestEngine."""

    def __init__(self, engine: BacktestEngine) -> None:
        self.engine = engine

    def audit(
        self,
        frame: BarFrame,
        signal_positions: np.ndarray,
        *,
        base_report: BacktestReport,
        symbols: Sequence[str],
        periods: int = 4,
        cost_multipliers: Sequence[float] = (1.0, 1.5, 2.0, 3.0),
    ) -> RobustnessAuditResult:
        if periods < 1:
            raise ValueError("periods must be positive")
        signals = np.asarray(signal_positions, dtype=np.float64)
        if signals.shape != frame.close.shape:
            raise ValueError("signal_positions must match the BarFrame shape")
        symbol_names = tuple(str(symbol) for symbol in symbols)
        equity = np.asarray(base_report.equity_curve, dtype=np.float64)
        returns = self._returns_from_equity(equity)

        subperiods = self._subperiods(
            equity=equity,
            actual_positions=self._portfolio_positions(base_report),
            periods=periods,
        )
        cost_stress = self._cost_stress(
            frame=frame,
            signals=signals,
            symbols=symbol_names,
            formula_tokens=base_report.formula_tokens,
            strategy_id=base_report.strategy_id,
            strategy_version=base_report.strategy_version,
            dataset_id=base_report.dataset_id,
            multipliers=cost_multipliers,
        )
        per_symbol = tuple(
            {
                "symbol": item.symbol,
                "total_return": float(item.metrics["total_return"]),
                "annualized_return": float(item.metrics["annualized_return"]),
                "max_drawdown": float(item.metrics["max_drawdown"]),
                "sharpe": float(item.metrics["sharpe"]),
                "turnover": float(item.metrics["turnover"]),
                "trade_count": int(item.metrics["trade_count"]),
                "total_cost": item.total_cost,
            }
            for item in base_report.per_symbol
        )
        monthly_returns, annual_returns = self._calendar_returns(
            frame.time[0],
            returns,
        )
        holding_period = self._holding_period(base_report)
        turnover_audit = self._turnover_audit(
            self._portfolio_positions(base_report)
        )
        issues = self._issues(
            subperiods=subperiods,
            cost_stress=cost_stress,
            holding_period=holding_period,
            turnover_audit=turnover_audit,
        )
        status = "passed"
        if any(item["final_equity"] <= 0 for item in subperiods):
            status = "failed"
        elif issues:
            status = "suspicious"

        return RobustnessAuditResult(
            status=status,
            issues=tuple(issues),
            subperiods=tuple(subperiods),
            cost_stress=tuple(cost_stress),
            per_symbol=per_symbol,
            monthly_returns=monthly_returns,
            annual_returns=annual_returns,
            holding_period=holding_period,
            turnover_audit=turnover_audit,
            metadata={
                "periods_per_year": int(
                    self.engine.config.periods_per_year
                    or 252
                ),
                "cost_multipliers": [float(value) for value in cost_multipliers],
                "audit_version": "alpha_lab_robustness_v1",
            },
        )

    def _subperiods(
        self,
        *,
        equity: np.ndarray,
        actual_positions: np.ndarray,
        periods: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        boundaries = np.array_split(np.arange(equity.size), periods)
        for index, indices in enumerate(boundaries, start=1):
            if indices.size == 0:
                continue
            start = int(indices[0])
            stop = int(indices[-1]) + 1
            segment_equity = equity[start:stop]
            base_equity = (
                float(equity[start - 1])
                if start > 0
                else float(segment_equity[0])
            )
            segment_returns = np.empty(segment_equity.size, dtype=np.float64)
            for offset, value in enumerate(segment_equity):
                previous = (
                    base_equity
                    if offset == 0
                    else float(segment_equity[offset - 1])
                )
                segment_returns[offset] = float(value / previous - 1.0)
            if start > 0:
                position_delta = actual_positions[start:stop]
                turnover = float(
                    np.abs(
                        np.diff(
                            np.concatenate(
                                [[actual_positions[start - 1]], position_delta]
                            )
                        )
                    ).sum()
                )
            else:
                position_delta = actual_positions[start:stop]
                turnover = float(
                    np.abs(
                        np.diff(np.concatenate([[0.0], position_delta]))
                    ).sum()
                )
            metrics = calculate_metrics(
                equity_curve=segment_equity,
                period_returns=segment_returns,
                turnover=turnover,
                trade_count=0,
                periods_per_year=(
                    self.engine.config.periods_per_year
                    or 252
                ),
                risk_free_rate=self.engine.config.risk_free_rate,
            )
            rows.append(
                {
                    "period": index,
                    "start_bar": start,
                    "end_bar": stop - 1,
                    "final_equity": float(segment_equity[-1]),
                    "total_return": float(metrics["total_return"]),
                    "annualized_return": float(metrics["annualized_return"]),
                    "max_drawdown": float(metrics["max_drawdown"]),
                    "sharpe": float(metrics["sharpe"]),
                    "turnover": float(metrics["turnover"]),
                }
            )
        return rows

    def _cost_stress(
        self,
        *,
        frame: BarFrame,
        signals: np.ndarray,
        symbols: tuple[str, ...],
        formula_tokens: Sequence[int],
        strategy_id: str,
        strategy_version: str,
        dataset_id: str,
        multipliers: Sequence[float],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for multiplier in multipliers:
            stressed_cost_model = self.engine.cost_model.with_stress(float(multiplier))
            report = self.engine._run_once(
                frame,
                signals,
                formula_tokens=tuple(int(token) for token in formula_tokens),
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                dataset_id=dataset_id,
                symbols=symbols,
                cost_model=stressed_cost_model,
            )
            rows.append(
                {
                    "multiplier": float(multiplier),
                    "final_equity": report.final_equity,
                    "total_return": float(report.metrics["total_return"]),
                    "annualized_return": float(
                        report.metrics["annualized_return"]
                    ),
                    "max_drawdown": float(report.metrics["max_drawdown"]),
                    "sharpe": float(report.metrics["sharpe"]),
                    "turnover": float(report.metrics["turnover"]),
                    "cost_total": float(report.cost_breakdown["total"]),
                    "profitable": report.final_equity > report.initial_equity,
                }
            )
        return rows

    @staticmethod
    def _calendar_returns(
        time: np.ndarray,
        returns: np.ndarray,
    ) -> tuple[dict[str, float], dict[str, float]]:
        dates = [RobustnessAuditor._date_label(value) for value in time]
        if any(date is None for date in dates):
            labels = [f"bucket_{index:04d}" for index in range(len(returns))]
            monthly: dict[str, list[float]] = {}
            annual: dict[str, list[float]] = {}
            for label, value in zip(labels, returns, strict=True):
                monthly.setdefault(label, []).append(float(value))
                annual.setdefault(label, []).append(float(value))
        else:
            monthly_groups: dict[str, list[float]] = {}
            annual_groups: dict[str, list[float]] = {}
            for date, value in zip(dates, returns, strict=True):
                assert date is not None
                monthly_groups.setdefault(date[:7], []).append(float(value))
                annual_groups.setdefault(date[:4], []).append(float(value))
            monthly = monthly_groups
            annual = annual_groups
        return (
            {
                key: float(np.prod(np.asarray(values, dtype=np.float64) + 1.0) - 1.0)
                for key, values in sorted(monthly.items())
            },
            {
                key: float(np.prod(np.asarray(values, dtype=np.float64) + 1.0) - 1.0)
                for key, values in sorted(annual.items())
            },
        )

    @staticmethod
    def _date_label(value: float) -> str | None:
        absolute = abs(float(value))
        if absolute >= 1e17:
            seconds = float(value) / 1e9
        elif absolute >= 1e14:
            seconds = float(value) / 1e6
        elif absolute >= 1e11:
            seconds = float(value) / 1e3
        elif absolute >= 1e9:
            seconds = float(value)
        else:
            return None
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _holding_period(report: BacktestReport) -> dict[str, float]:
        holding = np.asarray(
            [trade.holding_bars for trade in report.trade_log],
            dtype=np.float64,
        )
        if holding.size == 0:
            return {
                "count": 0.0,
                "average_bars": 0.0,
                "median_bars": 0.0,
                "min_bars": 0.0,
                "max_bars": 0.0,
            }
        return {
            "count": float(holding.size),
            "average_bars": float(np.mean(holding)),
            "median_bars": float(np.median(holding)),
            "min_bars": float(np.min(holding)),
            "max_bars": float(np.max(holding)),
        }

    @staticmethod
    def _turnover_audit(actual_positions: np.ndarray) -> dict[str, float]:
        turnover = np.abs(
            np.diff(np.concatenate([[0.0], actual_positions]))
        )
        return {
            "total": float(np.sum(turnover)),
            "average": float(np.mean(turnover)),
            "maximum": float(np.max(turnover, initial=0.0)),
            "nonzero_ratio": float(np.mean(turnover > 1e-12)),
        }

    @staticmethod
    def _portfolio_positions(report: BacktestReport) -> np.ndarray:
        if not report.per_symbol:
            return np.asarray([], dtype=np.float64)
        return np.mean(
            np.stack(
                [
                    np.asarray(item.actual_positions, dtype=np.float64)
                    for item in report.per_symbol
                ],
                axis=0,
            ),
            axis=0,
        )

    @staticmethod
    def _returns_from_equity(equity: np.ndarray) -> np.ndarray:
        values = np.asarray(equity, dtype=np.float64)
        returns = np.empty(values.size, dtype=np.float64)
        returns[0] = 0.0
        if values.size > 1:
            returns[1:] = values[1:] / values[:-1] - 1.0
        return returns

    @staticmethod
    def _issues(
        *,
        subperiods: list[dict[str, Any]],
        cost_stress: list[dict[str, Any]],
        holding_period: dict[str, float],
        turnover_audit: dict[str, float],
    ) -> list[str]:
        issues: list[str] = []
        if any(float(item["total_return"]) <= 0 for item in subperiods):
            issues.append("negative_subperiod_return")
        stressed = next(
            (item for item in cost_stress if float(item["multiplier"]) == 2.0),
            None,
        )
        if stressed is not None and not bool(stressed["profitable"]):
            issues.append("cost_stress_2x_unprofitable")
        if float(holding_period["count"]) == 0:
            issues.append("no_completed_trades")
        if float(turnover_audit["total"]) <= 1e-12:
            issues.append("zero_turnover")
        return issues

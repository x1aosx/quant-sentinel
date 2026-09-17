from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from ..data import BarFrame
from ..factor import FEATURE_REGISTRY, FORMULA_VOCAB, FactorRuntime, SignalKernel
from .costs import AShareCostModel, CostBreakdown
from .execution_model import AShareExecutionModel
from .metrics import calculate_metrics, infer_periods_per_year
from .report import (
    BacktestReport,
    ExecutionRecord,
    SymbolBacktestResult,
    TradeRecord,
    aggregate_cost_breakdown,
)


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 1_000_000.0
    periods_per_year: int | None = None
    risk_free_rate: float = 0.0
    include_robustness: bool = True
    position_epsilon: float = 1e-12
    feature_warmup_bars: int = 64

    def __post_init__(self) -> None:
        if self.initial_equity <= 0 or not np.isfinite(self.initial_equity):
            raise ValueError("initial_equity must be finite and positive")
        if self.periods_per_year is not None and self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")
        if not np.isfinite(self.risk_free_rate) or self.risk_free_rate <= -1:
            raise ValueError("risk_free_rate must be finite and greater than -1")
        if self.position_epsilon < 0:
            raise ValueError("position_epsilon cannot be negative")
        if self.feature_warmup_bars < 1:
            raise ValueError("feature_warmup_bars must be positive")


class BacktestEngine:
    """Backtest AlphaLab formula programs with explicit execution timing."""

    def __init__(
        self,
        formula_tokens: Sequence[int] | None = None,
        *,
        formula: Sequence[int] | None = None,
        factor_schema_version: str | None = None,
        cost_model: AShareCostModel | None = None,
        execution_model: AShareExecutionModel | None = None,
        factor_runtime: FactorRuntime | None = None,
        signal_kernel: SignalKernel | None = None,
        config: BacktestConfig | None = None,
        initial_equity: float | None = None,
        periods_per_year: int | None = None,
        risk_free_rate: float | None = None,
        include_robustness: bool | None = None,
    ) -> None:
        if formula_tokens is not None and formula is not None:
            raise ValueError("provide formula_tokens or formula, not both")
        resolved_formula = formula_tokens if formula_tokens is not None else formula
        if resolved_formula is None:
            raise ValueError("formula_tokens are required")
        if len(resolved_formula) == 0:
            raise ValueError("formula_tokens cannot be empty")

        base_config = config or BacktestConfig()
        self.config = BacktestConfig(
            initial_equity=(
                base_config.initial_equity
                if initial_equity is None
                else initial_equity
            ),
            periods_per_year=(
                base_config.periods_per_year
                if periods_per_year is None
                else periods_per_year
            ),
            risk_free_rate=(
                base_config.risk_free_rate
                if risk_free_rate is None
                else risk_free_rate
            ),
            include_robustness=(
                base_config.include_robustness
                if include_robustness is None
                else include_robustness
            ),
            position_epsilon=base_config.position_epsilon,
            feature_warmup_bars=base_config.feature_warmup_bars,
        )
        self.formula_tokens = tuple(int(token) for token in resolved_formula)
        self.factor_schema_version = factor_schema_version or FORMULA_VOCAB.schema_version
        self.cost_model = cost_model or AShareCostModel()
        self.execution_model = execution_model or AShareExecutionModel()
        self.factor_runtime = factor_runtime or FactorRuntime()
        self.signal_kernel = signal_kernel or SignalKernel()

    def run(
        self,
        frame: BarFrame,
        formula_tokens: Sequence[int] | None = None,
        *,
        strategy_id: str = "",
        strategy_version: str = "",
        dataset_id: str = "",
        symbols: Sequence[str] | None = None,
        include_robustness: bool | None = None,
        robustness_periods: int = 4,
        report_path: str | Path | None = None,
    ) -> BacktestReport:
        formula = (
            self.formula_tokens
            if formula_tokens is None
            else tuple(int(token) for token in formula_tokens)
        )
        features = self._compute_features(frame)
        factors = self.factor_runtime.evaluate(
            formula,
            features,
            self.factor_schema_version,
        )
        signal_positions = self.signal_kernel.positions(factors)
        return self.run_positions(
            frame,
            signal_positions,
            formula_tokens=formula,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            symbols=symbols,
            include_robustness=include_robustness,
            robustness_periods=robustness_periods,
            report_path=report_path,
        )

    def run_positions(
        self,
        frame: BarFrame,
        signal_positions: np.ndarray,
        *,
        formula_tokens: Sequence[int] | None = None,
        strategy_id: str = "",
        strategy_version: str = "",
        dataset_id: str = "",
        symbols: Sequence[str] | None = None,
        include_robustness: bool | None = None,
        robustness_periods: int = 4,
        report_path: str | Path | None = None,
    ) -> BacktestReport:
        signals = np.asarray(signal_positions, dtype=np.float64)
        if signals.shape != frame.close.shape:
            raise ValueError("signal_positions must match the BarFrame shape")
        if not np.isfinite(signals).all():
            raise ValueError("signal_positions must be finite")

        resolved_symbols = self._resolve_symbols(frame, symbols)
        report = self._run_once(
            frame,
            signals,
            formula_tokens=(
                self.formula_tokens
                if formula_tokens is None
                else tuple(int(token) for token in formula_tokens)
            ),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            symbols=resolved_symbols,
        )
        should_audit = (
            self.config.include_robustness
            if include_robustness is None
            else include_robustness
        )
        if should_audit:
            from .robustness import RobustnessAuditor

            audit = RobustnessAuditor(self).audit(
                frame,
                signals,
                base_report=report,
                symbols=resolved_symbols,
                periods=robustness_periods,
            )
            report = replace(report, robustness=audit.to_dict())

        if report_path is not None:
            report.write_json(report_path)
        return report

    def _run_once(
        self,
        frame: BarFrame,
        signal_positions: np.ndarray,
        *,
        formula_tokens: tuple[int, ...],
        strategy_id: str,
        strategy_version: str,
        dataset_id: str,
        symbols: tuple[str, ...],
        cost_model: AShareCostModel | None = None,
    ) -> BacktestReport:
        active_cost_model = cost_model or self.cost_model
        sessions = self.execution_model.session_keys(frame.time[0])
        symbol_results: list[SymbolBacktestResult] = []
        standalone_runs: list[dict[str, Any]] = []
        portfolio_runs: list[dict[str, Any]] = []
        trade_log: list[TradeRecord] = []
        executions: list[ExecutionRecord] = []
        all_cost_items: list[CostBreakdown] = []
        n_symbols = frame.n_symbols

        for index, symbol in enumerate(symbols):
            standalone = self._run_symbol(
                symbol=symbol,
                frame=frame,
                signal_positions=signal_positions[index],
                sessions=sessions,
                position_scale=1.0,
                cost_model=active_cost_model,
            )
            portfolio = self._run_symbol(
                symbol=symbol,
                frame=frame,
                signal_positions=signal_positions[index],
                sessions=sessions,
                position_scale=1.0 / max(n_symbols, 1),
                cost_model=active_cost_model,
            )
            standalone_runs.append(standalone)
            portfolio_runs.append(portfolio)
            trade_log.extend(standalone["trades"])
            executions.extend(standalone["executions"])
            all_cost_items.extend(portfolio["cost_items"])
            symbol_results.append(
                SymbolBacktestResult(
                    symbol=symbol,
                    metrics=standalone["metrics"],
                    equity_curve=tuple(float(v) for v in standalone["equity"]),
                    drawdown_curve=tuple(
                        float(v) for v in standalone["drawdown"]
                    ),
                    target_positions=tuple(
                        float(v) for v in standalone["target_positions"]
                    ),
                    actual_positions=tuple(
                        float(v) for v in standalone["actual_positions"]
                    ),
                    total_cost=float(standalone["total_cost"]),
                    cost_breakdown=dict(standalone["cost_breakdown"]),
                )
            )

        portfolio_returns = np.mean(
            np.stack([item["returns"] for item in portfolio_runs], axis=0),
            axis=0,
        )
        portfolio_equity = self.config.initial_equity * np.cumprod(
            1.0 + portfolio_returns
        )
        if not np.isfinite(portfolio_equity).all() or np.any(portfolio_equity <= 0):
            raise ValueError("portfolio equity curve must remain finite and positive")
        portfolio_drawdown = self._drawdown_curve(portfolio_equity)
        portfolio_turnover = float(
            np.mean([item["turnover"] for item in portfolio_runs])
        )
        metrics = calculate_metrics(
            equity_curve=portfolio_equity,
            period_returns=portfolio_returns,
            turnover=portfolio_turnover,
            trade_count=len(trade_log),
            periods_per_year=(
                self.config.periods_per_year
                or infer_periods_per_year(frame.timeframe)
            ),
            risk_free_rate=self.config.risk_free_rate,
        )
        cost_breakdown = aggregate_cost_breakdown(all_cost_items)
        run_id = self._run_id(
            frame=frame,
            formula_tokens=formula_tokens,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            cost_multiplier=(
                active_cost_model.config.commission_rate
                / max(self.cost_model.config.commission_rate, 1e-15)
                if self.cost_model.config.commission_rate
                else 1.0
            ),
        )
        return BacktestReport(
            run_id=run_id,
            formula_tokens=formula_tokens,
            factor_schema_version=self.factor_schema_version,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            dataset_id=dataset_id,
            initial_equity=float(portfolio_equity[0]),
            final_equity=float(portfolio_equity[-1]),
            metrics=metrics,
            equity_curve=tuple(float(value) for value in portfolio_equity),
            drawdown_curve=tuple(float(value) for value in portfolio_drawdown),
            trade_log=tuple(trade_log),
            executions=tuple(executions),
            per_symbol=tuple(symbol_results),
            cost_breakdown=cost_breakdown,
            execution_model=self.execution_model.to_dict(),
            cost_model=active_cost_model.to_dict(),
        )

    def _run_symbol(
        self,
        *,
        symbol: str,
        frame: BarFrame,
        signal_positions: np.ndarray,
        sessions: np.ndarray,
        position_scale: float,
        cost_model: AShareCostModel,
    ) -> dict[str, Any]:
        signals = np.asarray(signal_positions, dtype=np.float64)
        if signals.shape != (frame.n_bars,):
            raise ValueError("symbol signal must have shape [T]")
        scheduled = self.execution_model.schedule(signals[None, :])[0]
        target_positions = scheduled * position_scale

        open_prices = frame.open[0]
        close_prices = frame.close[0]
        times = frame.time[0]
        n_bars = frame.n_bars
        initial_equity = self.config.initial_equity
        equity = np.empty(n_bars, dtype=np.float64)
        returns = np.empty(n_bars, dtype=np.float64)
        actual_positions = np.zeros(n_bars, dtype=np.float64)
        executions: list[ExecutionRecord] = []
        trades: list[TradeRecord] = []
        cost_items: list[CostBreakdown] = []

        current_position = 0.0
        entry_session: int | str | None = None
        epsilon = self.config.position_epsilon
        for bar_index in range(n_bars):
            if bar_index == 0:
                equity_open = initial_equity
            else:
                overnight_return = (
                    float(open_prices[bar_index] / close_prices[bar_index - 1])
                    - 1.0
                )
                equity_open = equity[bar_index - 1] * (
                    1.0 + current_position * overnight_return
                )
            if not np.isfinite(equity_open) or equity_open <= 0:
                raise ValueError("equity must remain finite and positive")

            decision = self.execution_model.resolve(
                raw_target=float(target_positions[bar_index]),
                current_position=current_position,
                bar_index=bar_index,
                entry_session=entry_session,
                session=sessions[bar_index],
            )
            next_position = decision.target_position
            exposure_delta = next_position - current_position
            cost = cost_model.calculate(exposure_delta, equity_open)
            equity_after_cost = equity_open - cost.total
            if not np.isfinite(equity_after_cost) or equity_after_cost <= 0:
                raise ValueError("transaction costs exhausted the account")

            intraday_return = float(close_prices[bar_index] / open_prices[bar_index]) - 1.0
            equity_close = equity_after_cost * (
                1.0 + next_position * intraday_return
            )
            if not np.isfinite(equity_close) or equity_close <= 0:
                raise ValueError("equity must remain finite and positive")

            equity[bar_index] = equity_close
            returns[bar_index] = (
                equity_close / initial_equity - 1.0
                if bar_index == 0
                else equity_close / equity[bar_index - 1] - 1.0
            )
            actual_positions[bar_index] = next_position
            cost_items.append(cost)

            if decision.changed or decision.t_plus_one_blocked:
                signal_bar = bar_index - self.execution_model.execution_lag_bars
                executions.append(
                    ExecutionRecord(
                        symbol=symbol,
                        signal_bar=signal_bar,
                        execution_bar=bar_index,
                        execution_time=float(times[bar_index]),
                        from_position=float(current_position),
                        to_position=float(next_position),
                        raw_target=float(decision.raw_target),
                        price=float(open_prices[bar_index]),
                        equity_before=float(equity_open),
                        equity_after_cost=float(equity_after_cost),
                        turnover=float(cost.turnover),
                        cost=cost,
                        short_blocked=decision.short_blocked,
                        t_plus_one_blocked=decision.t_plus_one_blocked,
                        reason=decision.reason,
                    )
                )

            if current_position <= epsilon and next_position > epsilon:
                entry_session = sessions[bar_index]
            current_position = next_position

        trades = self._extract_trades(
            symbol=symbol,
            equity=equity,
            actual_positions=actual_positions,
            open_prices=open_prices,
            times=times,
            epsilon=epsilon,
        )
        drawdown = self._drawdown_curve(equity)
        turnover = float(np.abs(np.diff(np.concatenate([[0.0], actual_positions]))).sum())
        metrics = calculate_metrics(
            equity_curve=equity,
            period_returns=returns,
            turnover=turnover,
            trade_count=len(trades),
            periods_per_year=(
                self.config.periods_per_year
                or infer_periods_per_year(frame.timeframe)
            ),
            risk_free_rate=self.config.risk_free_rate,
        )
        if not np.isfinite(equity).all():
            raise ValueError("equity curve must be finite")
        return {
            "symbol": symbol,
            "metrics": metrics,
            "equity": equity,
            "returns": returns,
            "drawdown": drawdown,
            "target_positions": target_positions,
            "actual_positions": actual_positions,
            "turnover": turnover,
            "trades": trades,
            "executions": executions,
            "cost_items": cost_items,
            "total_cost": float(sum(item.total for item in cost_items)),
            "cost_breakdown": aggregate_cost_breakdown(cost_items),
        }

    def _compute_features(self, frame: BarFrame) -> np.ndarray:
        minimum_bars = self.config.feature_warmup_bars
        if frame.n_bars >= minimum_bars:
            features = FEATURE_REGISTRY.compute(frame)
        else:
            warmup = minimum_bars - frame.n_bars
            extended = BarFrame(
                symbol=frame.symbol,
                timeframe=frame.timeframe,
                open=np.concatenate(
                    [np.repeat(frame.open[:, :1], warmup, axis=1), frame.open],
                    axis=1,
                ),
                high=np.concatenate(
                    [np.repeat(frame.high[:, :1], warmup, axis=1), frame.high],
                    axis=1,
                ),
                low=np.concatenate(
                    [np.repeat(frame.low[:, :1], warmup, axis=1), frame.low],
                    axis=1,
                ),
                close=np.concatenate(
                    [np.repeat(frame.close[:, :1], warmup, axis=1), frame.close],
                    axis=1,
                ),
                volume=np.concatenate(
                    [np.repeat(frame.volume[:, :1], warmup, axis=1), frame.volume],
                    axis=1,
                ),
                time=np.concatenate(
                    [np.repeat(frame.time[:, :1], warmup, axis=1), frame.time],
                    axis=1,
                ),
                is_closed=np.concatenate(
                    [
                        np.repeat(frame.is_closed[:, :1], warmup, axis=1),
                        frame.is_closed,
                    ],
                    axis=1,
                ),
                source=frame.source,
                adjustment=frame.adjustment,
            )
            features = FEATURE_REGISTRY.compute(extended)[:, :, warmup:]

        expected = (
            frame.n_symbols,
            self.factor_runtime.feature_count,
            frame.n_bars,
        )
        if features.shape != expected:
            raise ValueError(
                f"feature registry returned {features.shape}, expected {expected}"
            )
        if not np.isfinite(features).all():
            raise ValueError("feature registry returned non-finite values")
        return np.asarray(features, dtype=np.float64)

    @staticmethod
    def _drawdown_curve(equity: np.ndarray) -> np.ndarray:
        values = np.asarray(equity, dtype=np.float64)
        peaks = np.maximum.accumulate(values)
        return np.divide(
            peaks - values,
            peaks,
            out=np.zeros_like(values),
            where=peaks > 0,
        )

    @staticmethod
    def _extract_trades(
        *,
        symbol: str,
        equity: np.ndarray,
        actual_positions: np.ndarray,
        open_prices: np.ndarray,
        times: np.ndarray,
        epsilon: float,
    ) -> list[TradeRecord]:
        positions = np.asarray(actual_positions, dtype=np.float64)
        signs = np.zeros(positions.shape, dtype=np.int8)
        signs[positions > epsilon] = 1
        signs[positions < -epsilon] = -1
        trades: list[TradeRecord] = []
        start: int | None = None
        current_sign = 0
        for bar_index, sign in enumerate(signs):
            if sign == current_sign:
                continue
            if current_sign != 0 and start is not None:
                exit_bar = bar_index
                base_equity = (
                    float(equity[start])
                    if start == 0
                    else float(equity[start - 1])
                )
                trades.append(
                    TradeRecord(
                        symbol=symbol,
                        direction="long" if current_sign > 0 else "short",
                        entry_bar=start,
                        exit_bar=exit_bar,
                        entry_time=float(times[start]),
                        exit_time=float(times[exit_bar]),
                        entry_price=float(open_prices[start]),
                        exit_price=float(open_prices[exit_bar]),
                        holding_bars=exit_bar - start + 1,
                        pnl=float(equity[exit_bar] / base_equity - 1.0),
                    )
                )
            start = bar_index if sign != 0 else None
            current_sign = int(sign)

        if current_sign != 0 and start is not None:
            exit_bar = len(positions) - 1
            base_equity = (
                float(equity[start])
                if start == 0
                else float(equity[start - 1])
            )
            trades.append(
                TradeRecord(
                    symbol=symbol,
                    direction="long" if current_sign > 0 else "short",
                    entry_bar=start,
                    exit_bar=exit_bar,
                    entry_time=float(times[start]),
                    exit_time=float(times[exit_bar]),
                    entry_price=float(open_prices[start]),
                    exit_price=float(open_prices[exit_bar]),
                    holding_bars=exit_bar - start + 1,
                    pnl=float(equity[exit_bar] / base_equity - 1.0),
                )
            )
        return trades

    @staticmethod
    def _resolve_symbols(
        frame: BarFrame,
        symbols: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if symbols is not None:
            resolved = tuple(str(symbol) for symbol in symbols)
            if len(resolved) != frame.n_symbols:
                raise ValueError("symbols must match the BarFrame symbol dimension")
            return resolved
        if frame.n_symbols == 1:
            return (frame.symbol,)
        return tuple(f"{frame.symbol}_{index}" for index in range(frame.n_symbols))

    @staticmethod
    def _run_id(
        *,
        frame: BarFrame,
        formula_tokens: Sequence[int],
        strategy_id: str,
        strategy_version: str,
        dataset_id: str,
        cost_multiplier: float,
    ) -> str:
        payload = (
            f"{frame.content_hash()}:{list(formula_tokens)}:{strategy_id}:"
            f"{strategy_version}:{dataset_id}:{cost_multiplier:.12g}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

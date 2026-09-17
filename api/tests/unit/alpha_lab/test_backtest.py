from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np

from xquant.alpha_lab.backtest import (
    AShareCostModel,
    AShareExecutionConfig,
    AShareExecutionModel,
    BacktestEngine,
)
from xquant.alpha_lab.data import BarFrame


def _frame(n_bars: int = 120, *, same_session: bool = False) -> BarFrame:
    x = np.arange(n_bars, dtype=np.float64)
    close = 100.0 + x * 0.04 + np.sin(x / 7.0) * 0.8
    open_ = close - 0.05 + np.cos(x / 9.0) * 0.02
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = 1_000.0 + x
    if same_session:
        start = int(datetime(2026, 1, 5, 9, 30, tzinfo=UTC).timestamp())
        time = start + x.astype(np.int64) * 300
    else:
        time = x
    return BarFrame(
        symbol="TEST",
        timeframe="1d" if not same_session else "5m",
        open=open_[None, :],
        high=high[None, :],
        low=low[None, :],
        close=close[None, :],
        volume=volume[None, :],
        time=time[None, :],
        is_closed=np.ones((1, n_bars), dtype=bool),
        source="test",
    )


def _signals(frame: BarFrame) -> np.ndarray:
    signals = np.zeros(frame.close.shape, dtype=np.float64)
    signals[0, 5:30] = 0.7
    signals[0, 40:60] = 1.0
    return signals


def test_execution_timing_uses_next_bar_open_without_future_leakage() -> None:
    frame = _frame()
    signals = np.zeros(frame.close.shape, dtype=np.float64)
    signals[0, 3:8] = 1.0
    engine = BacktestEngine(
        formula_tokens=[0],
        cost_model=AShareCostModel(
            commission_rate=0.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_rate=0.0,
        ),
    )

    report = engine.run_positions(frame, signals, include_robustness=False)

    assert report.executions
    first = report.executions[0]
    assert first.signal_bar == 3
    assert first.execution_bar == 4
    assert first.price == frame.open[0, 4]
    assert report.per_symbol[0].actual_positions[3] == 0.0
    assert report.per_symbol[0].actual_positions[4] == 1.0
    assert all(
        item.execution_bar == item.signal_bar + 1
        for item in report.executions
    )


def test_costs_increase_strictly_with_actual_turnover() -> None:
    model = AShareCostModel()
    low = model.calculate(delta=0.1, equity=1_000_000.0)
    high = model.calculate(delta=0.2, equity=1_000_000.0)
    buy = model.calculate(turnover=0.2, equity=1_000_000.0, side="buy")
    sell = model.calculate(turnover=0.2, equity=1_000_000.0, side="sell")

    assert high.total > low.total
    assert high.commission > low.commission
    assert high.transfer_fee > low.transfer_fee
    assert high.slippage > low.slippage
    assert sell.stamp_duty > buy.stamp_duty
    assert sell.total > buy.total


def test_short_ban_is_default_and_configurable() -> None:
    default_model = AShareExecutionModel()
    long_only = default_model.resolve(
        raw_target=-0.8,
        current_position=0.0,
        bar_index=1,
    )
    short_model = AShareExecutionModel(allow_short=True)
    short_allowed = short_model.resolve(
        raw_target=-0.8,
        current_position=0.0,
        bar_index=1,
    )
    frame = _frame()
    signals = np.full(frame.close.shape, -1.0)
    report = BacktestEngine(formula_tokens=[0]).run_positions(
        frame,
        signals,
        include_robustness=False,
    )

    assert long_only.target_position == 0.0
    assert long_only.short_blocked
    assert short_allowed.target_position == -0.8
    assert not short_allowed.short_blocked
    assert all(
        position >= 0.0
        for item in report.per_symbol
        for position in item.actual_positions
    )


def test_t_plus_one_blocks_same_session_reduction() -> None:
    frame = _frame(same_session=True)
    session = AShareExecutionModel.session_keys(frame.time[0])[1]
    locked = AShareExecutionModel().resolve(
        raw_target=0.0,
        current_position=0.5,
        bar_index=1,
        entry_session=session,
        session=session,
    )
    unlocked = AShareExecutionModel(
        config=AShareExecutionConfig(t_plus_one=False)
    ).resolve(
        raw_target=0.0,
        current_position=0.5,
        bar_index=1,
        entry_session=session,
        session=session,
    )

    assert locked.target_position == 0.5
    assert locked.t_plus_one_blocked
    assert unlocked.target_position == 0.0
    assert not unlocked.t_plus_one_blocked


def test_metrics_robustness_and_report_are_finite_and_serializable() -> None:
    frame = _frame()
    engine = BacktestEngine(formula_tokens=[0])
    report = engine.run_positions(
        frame,
        _signals(frame),
        strategy_id="strategy",
        strategy_version="1.0.0",
        dataset_id="dataset",
        include_robustness=True,
    )

    assert np.isfinite(report.equity_curve).all()
    assert np.isfinite(report.drawdown_curve).all()
    assert np.isfinite(report.final_equity)
    assert report.final_equity > 0
    assert {
        "equity",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "sharpe",
        "turnover",
        "trade_count",
    }.issubset(report.metrics)
    assert {
        "subperiods",
        "cost_stress",
        "per_symbol",
        "monthly_returns",
        "annual_returns",
        "holding_period",
    }.issubset(report.robustness)
    payload = json.loads(report.to_json())
    assert payload["strategy_id"] == "strategy"
    assert payload["executions"]
    stressed = {
        float(item["multiplier"]): item
        for item in report.robustness["cost_stress"]
    }
    assert stressed[2.0]["cost_total"] > stressed[1.0]["cost_total"]


def test_multi_symbol_backtest_uses_each_symbol_price_series() -> None:
    base = _frame()
    second_close = base.close[0] * 1.5 + 10.0
    frame = BarFrame(
        symbol="PAIR",
        timeframe=base.timeframe,
        open=np.stack([base.open[0], base.open[0] * 1.5 + 10.0], axis=0),
        high=np.stack([base.high[0], base.high[0] * 1.5 + 10.0], axis=0),
        low=np.stack([base.low[0], base.low[0] * 1.5 + 10.0], axis=0),
        close=np.stack([base.close[0], second_close], axis=0),
        volume=np.stack([base.volume[0], base.volume[0] * 2.0], axis=0),
        time=np.stack([base.time[0], base.time[0]], axis=0),
        is_closed=np.stack([base.is_closed[0], base.is_closed[0]], axis=0),
        source="test",
    )
    signals = np.zeros(frame.close.shape, dtype=np.float64)
    signals[0, 3:8] = 1.0
    signals[1, 6:10] = 1.0

    report = BacktestEngine(formula_tokens=[0]).run_positions(
        frame,
        signals,
        symbols=["A", "B"],
        include_robustness=False,
    )

    second_execution = next(
        item for item in report.executions if item.symbol == "B"
    )
    assert second_execution.execution_bar == 7
    assert second_execution.price == frame.open[1, 7]

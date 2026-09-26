from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np


def calculate_metrics(
    *,
    equity_curve: np.ndarray,
    period_returns: np.ndarray,
    turnover: float,
    trade_count: int,
    periods_per_year: int,
    risk_free_rate: float = 0.0,
) -> dict[str, Any]:
    """Compute finite, machine-readable performance metrics."""

    equity = np.asarray(equity_curve, dtype=np.float64).reshape(-1)
    returns = np.asarray(period_returns, dtype=np.float64).reshape(-1)
    if equity.size == 0 or returns.size == 0:
        raise ValueError("equity_curve and period_returns cannot be empty")
    if equity.shape != returns.shape:
        raise ValueError("equity_curve and period_returns must have the same length")
    if not np.isfinite(equity).all() or not np.isfinite(returns).all():
        raise ValueError("metrics inputs must be finite")
    if np.any(equity <= 0):
        raise ValueError("equity_curve must remain positive")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")

    initial_equity = float(equity[0])
    final_equity = float(equity[-1])
    total_return = final_equity / initial_equity - 1.0
    elapsed_periods = max(returns.size - 1, 1)
    annualized_return = (
        (final_equity / initial_equity) ** (periods_per_year / elapsed_periods)
        - 1.0
    )

    peaks = np.maximum.accumulate(equity)
    drawdowns = np.divide(
        peaks - equity,
        peaks,
        out=np.zeros_like(equity),
        where=peaks > 0,
    )
    max_drawdown = float(drawdowns.max(initial=0.0))

    periodic_risk_free = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess_returns = returns - periodic_risk_free
    volatility = float(np.std(returns, ddof=0))
    excess_std = float(np.std(excess_returns, ddof=0))
    sharpe = (
        float(np.mean(excess_returns) / excess_std * np.sqrt(periods_per_year))
        if excess_std > 1e-12
        else 0.0
    )
    # Sortino uses only the downside deviation so upside volatility is not punished.
    downside = excess_returns[excess_returns < 0.0]
    downside_deviation = (
        float(np.sqrt(np.mean(np.square(downside)))) if downside.size else 0.0
    )
    sortino = (
        float(np.mean(excess_returns) / downside_deviation * np.sqrt(periods_per_year))
        if downside_deviation > 1e-12
        else 0.0
    )
    # Calmar: annualized return per unit of worst peak-to-trough loss.
    calmar = float(annualized_return / max_drawdown) if max_drawdown > 1e-12 else 0.0
    annualized_volatility = volatility * float(np.sqrt(periods_per_year))
    average_turnover = float(turnover / returns.size)

    values = (
        initial_equity,
        final_equity,
        total_return,
        annualized_return,
        max_drawdown,
        sharpe,
        sortino,
        calmar,
        annualized_volatility,
        float(turnover),
        average_turnover,
    )
    finite_values = [value if np.isfinite(value) else 0.0 for value in values]
    (
        initial_equity,
        final_equity,
        total_return,
        annualized_return,
        max_drawdown,
        sharpe,
        sortino,
        calmar,
        annualized_volatility,
        turnover_value,
        average_turnover,
    ) = finite_values
    return {
        "initial_equity": float(initial_equity),
        "final_equity": float(final_equity),
        "total_return": float(total_return),
        "annualized_return": float(annualized_return),
        "annualized_volatility": float(annualized_volatility),
        "max_drawdown": float(max_drawdown),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "turnover": float(turnover_value),
        "average_turnover": float(average_turnover),
        "trade_count": int(trade_count),
        "equity": [float(value) for value in equity],
    }


def trade_statistics(
    *,
    pnls: Sequence[float],
    holding_bars: Sequence[float] | None = None,
) -> dict[str, float]:
    """Trade-level statistics: win rate, profit/loss ratio and holding period.

    ``pnls`` are per-trade returns (fractional). ``profit_loss_ratio`` is the
    average winning trade divided by the average losing trade; ``profit_factor``
    is the gross profit divided by the gross loss.
    """

    values = np.asarray(list(pnls), dtype=np.float64).reshape(-1)
    values = values[np.isfinite(values)]
    empty = {
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "profit_factor": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "average_holding_bars": 0.0,
        "win_count": 0.0,
        "loss_count": 0.0,
    }
    if values.size == 0:
        return empty

    wins = values[values > 0.0]
    losses = values[values < 0.0]
    average_win = float(np.mean(wins)) if wins.size else 0.0
    average_loss = float(np.mean(losses)) if losses.size else 0.0
    gross_profit = float(np.sum(wins)) if wins.size else 0.0
    gross_loss = float(np.sum(losses)) if losses.size else 0.0

    holding = np.asarray(list(holding_bars or []), dtype=np.float64).reshape(-1)
    holding = holding[np.isfinite(holding)]
    return {
        "win_rate": float(wins.size / values.size),
        "profit_loss_ratio": (
            float(average_win / abs(average_loss)) if average_loss < -1e-12 else 0.0
        ),
        "profit_factor": (
            float(gross_profit / abs(gross_loss)) if gross_loss < -1e-12 else 0.0
        ),
        "average_win": average_win,
        "average_loss": average_loss,
        "best_trade": float(np.max(values)),
        "worst_trade": float(np.min(values)),
        "average_holding_bars": float(np.mean(holding)) if holding.size else 0.0,
        "win_count": float(wins.size),
        "loss_count": float(losses.size),
    }


def rolling_sharpe_series(
    period_returns: np.ndarray,
    *,
    periods_per_year: int,
    window: int | None = None,
) -> list[float]:
    """Rolling Sharpe aligned with the equity curve (0.0 while the window is open)."""

    returns = np.asarray(period_returns, dtype=np.float64).reshape(-1)
    size = int(returns.size)
    if size == 0:
        return []
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    resolved_window = window if window is not None else max(5, periods_per_year // 12)
    resolved_window = int(max(2, min(resolved_window, size)))
    scale = float(np.sqrt(periods_per_year))
    series = np.zeros(size, dtype=np.float64)
    for index in range(resolved_window - 1, size):
        sample = returns[index - resolved_window + 1 : index + 1]
        deviation = float(np.std(sample, ddof=0))
        series[index] = (
            float(np.mean(sample) / deviation * scale) if deviation > 1e-12 else 0.0
        )
    return [float(value) if np.isfinite(value) else 0.0 for value in series]


def time_axis_labels(frame: Any) -> tuple[list[str], str]:
    """Return x-axis labels for a ``BarFrame`` and the kind of label produced.

    Dataset frames expose exchange session ids; frames built directly from
    arrays only carry a synthetic bar index, so callers can label the axis
    honestly instead of inventing dates.
    """

    session = getattr(frame, "session", None)
    if session is not None:
        labels = [str(value) for value in np.asarray(session)[0]]
        if any(label.strip() for label in labels):
            return labels, "session"
    return [str(value) for value in np.asarray(frame.time)[0]], "bar_index"


def infer_periods_per_year(timeframe: str) -> int:
    normalized = str(timeframe or "1d").strip().lower()
    mapping = {
        "1m": 252 * 240,
        "5m": 252 * 48,
        "15m": 252 * 16,
        "30m": 252 * 8,
        "1h": 252 * 4,
        "60m": 252 * 4,
        "4h": 252,
        "1d": 252,
        "day": 252,
        "1w": 52,
        "week": 52,
        "1mo": 12,
        "month": 12,
    }
    return mapping.get(normalized, 252)

from __future__ import annotations

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
    annualized_volatility = volatility * float(np.sqrt(periods_per_year))
    average_turnover = float(turnover / returns.size)

    values = (
        initial_equity,
        final_equity,
        total_return,
        annualized_return,
        max_drawdown,
        sharpe,
        annualized_volatility,
        float(turnover),
        average_turnover,
    )
    finite_values = [
        value if np.isfinite(value) else 0.0
        for value in values
    ]
    (
        initial_equity,
        final_equity,
        total_return,
        annualized_return,
        max_drawdown,
        sharpe,
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
        "turnover": float(turnover_value),
        "average_turnover": float(average_turnover),
        "trade_count": int(trade_count),
        "equity": [float(value) for value in equity],
    }


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

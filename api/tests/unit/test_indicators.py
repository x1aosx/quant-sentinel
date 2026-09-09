from __future__ import annotations

from datetime import datetime

import numpy as np

from xquant.domain.models import Bar
from xquant.features.indicators import atr_series, compute_features, ema_series
from xquant.marketdata.ohlc import validate_ohlcv


def _bar(i: int, close: float) -> Bar:
    return Bar(
        instrument_id="T",
        session_id=f"S{i:04d}",
        open=close - 0.1,
        high=close + 0.2,
        low=close - 0.3,
        close=close,
        volume_shares=1000,
        turnover_currency=1000 * close,
        source="demo",
        received_at=datetime(2026, 1, 1),
        available_at=datetime(2026, 1, 1),
        revision_id=f"r{i}",
    )


def test_ema_seed_and_recursive() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=float)
    result = ema_series(values, 3)
    assert result[2] == 2.0
    assert result[3] > 2.0


def test_atr_seed_uses_period_mean() -> None:
    highs = np.arange(10.0, 25.0, dtype=float)
    lows = highs - 1.0
    closes = highs - 0.5
    result = atr_series(highs, lows, closes, 14)
    assert abs(result[13] - 1.4642857142857142) < 1e-9
    assert np.isfinite(result[14])


def test_ohlcv_validation_rejects_invalid_order() -> None:
    bars = [_bar(1, 10.0), _bar(2, 10.5), _bar(1, 11.0)]
    try:
        validate_ohlcv(bars)
    except ValueError:
        return
    raise AssertionError("expected out-of-order session rejection")


def test_compute_features_has_expected_columns() -> None:
    bars = [_bar(i, 10.0 + i * 0.1) for i in range(30)]
    df = compute_features(bars)
    assert "ema20" in df.columns
    assert "atr14" in df.columns
    assert "clv" in df.columns
    assert df["ema20"].notna().sum() > 0

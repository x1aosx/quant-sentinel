from __future__ import annotations

import numpy as np
import pandas as pd


def ema_series(values: np.ndarray, span: int) -> np.ndarray:
    if len(values) == 0:
        return np.array([])
    result = np.empty_like(values, dtype=float)
    result[:span] = np.nan
    if len(values) < span:
        return result
    seed = float(np.mean(values[:span]))
    result[span - 1] = seed
    alpha = 2.0 / (span + 1.0)
    prev = seed
    for i in range(span, len(values)):
        prev = alpha * float(values[i]) + (1.0 - alpha) * prev
        result[i] = prev
    return result


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    result = np.full(n, np.nan)
    if n == 0:
        return result
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    if n < period:
        return result
    seed = float(np.mean(tr[:period]))
    result[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = ((period - 1) * prev + tr[i]) / period
        result[i] = prev
    return result


def compute_features(bars: list) -> pd.DataFrame:
    rows = [
        {
            "session_id": b.session_id,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume_shares,
            "turnover": b.turnover_currency,
        }
        for b in bars
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    for span in (20, 60, 120):
        df[f"ema{span}"] = ema_series(close, span)
    df["atr14"] = atr_series(high, low, close, 14)
    rng = high - low
    df["clv"] = np.where(rng > 0, (close - low) / rng, 0.5)
    body = np.abs(close - open_)
    df["body_ratio"] = np.where(rng > 0, body / rng, 0.0)
    prev_close = df["close"].shift(1)
    df["true_range"] = np.maximum.reduce(
        [df["high"] - df["low"], np.abs(df["high"] - prev_close), np.abs(df["low"] - prev_close)]
    )
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    df["overlap"] = np.where(
        (prev_high > df["low"]) & (prev_low < df["high"]),
        (np.minimum(df["high"], prev_high) - np.maximum(df["low"], prev_low))
        / (np.maximum(df["high"], prev_high) - np.minimum(df["low"], prev_low)),
        1.0,
    )
    df["overlap10"] = df["overlap"].rolling(10).mean()
    diff = df["close"].diff()
    er_num = np.abs(df["close"] - df["close"].shift(20))
    er_den = diff.abs().rolling(20).sum()
    df["er20"] = np.where(er_den > 0, er_num / er_den, 0.0)
    df["slope20"] = (df["ema20"] - df["ema20"].shift(5)) / df["atr14"]
    df["adv20"] = df["turnover"].rolling(20).mean()
    return df

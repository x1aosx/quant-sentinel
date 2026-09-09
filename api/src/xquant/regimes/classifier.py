from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RegimeResult:
    regime: str
    reason_codes: tuple[str, ...]


def classify_regime(row) -> RegimeResult:
    if row.get("data_valid") is False:
        return RegimeResult("INVALID", ("DATA_INVALID",))
    overlap10 = row.get("overlap10")
    er20 = row.get("er20")
    slope20 = row.get("slope20")
    close = row.get("close")
    ema20 = row.get("ema20")
    ema60 = row.get("ema60")
    if overlap10 is None or er20 is None or slope20 is None:
        return RegimeResult("INVALID", ("FEATURE_MISSING",))
    if overlap10 >= 0.65 and er20 <= 0.20 and abs(slope20) <= 0.25:
        return RegimeResult("CHAOS", ("OVERLAP_HIGH", "ER_LOW", "SLOPE_FLAT"))
    if close is not None and ema20 is not None and ema60 is not None:
        if close > ema20 > ema60 and slope20 >= 0.25 and er20 >= 0.25:
            return RegimeResult("UPTREND", ("CLOSE_GT_EMA20", "EMA20_GT_EMA60", "SLOPE_UP", "ER_UP"))
        if close < ema20 < ema60 and slope20 <= -0.25:
            return RegimeResult("DOWNTREND", ("CLOSE_LT_EMA20", "EMA20_LT_EMA60", "SLOPE_DOWN"))
    return RegimeResult("NEUTRAL", ("NO_STRONG_TREND",))


def market_gate(benchmark_row) -> str:
    close = benchmark_row.get("close")
    ema120 = benchmark_row.get("ema120")
    ema20 = benchmark_row.get("ema20")
    lag5 = benchmark_row.get("ema20_lag5")
    if close is None or ema120 is None or ema20 is None or lag5 is None:
        return "UNKNOWN"
    if close > ema120 and ema20 >= lag5:
        return "RISK_ON"
    return "RISK_OFF"


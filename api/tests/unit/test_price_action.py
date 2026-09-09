from __future__ import annotations

import math

import pytest

from xquant.analysis.price_action import analyze_price_action


def _bar(
    index: int, open_: float, close: float, low: float | None = None, high: float | None = None
):
    low = open_ if low is None else low
    high = close if high is None else high
    return {
        "session_id": f"s{index:03d}",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": "1000",
    }


def _uptrend_pullback_bars() -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for cycle in range(14):
        base = 100.0 + cycle * 0.8
        leg = [
            (base, base + 0.55, base, base + 0.65),
            (base + 0.55, base + 0.80, base + 0.35, base + 0.95),
            (base + 0.80, base + 0.30, base + 0.05, base + 0.85),
            (base + 0.30, base + 0.75, base + 0.15, base + 0.85),
        ]
        for open_, close, low, high in leg:
            bars.append(_bar(len(bars), open_, close, low, high))
    pullback = [111.0, 110.45, 109.9, 109.55]
    for offset, close in enumerate(pullback):
        open_ = 111.15 if offset == 0 else pullback[offset - 1]
        bars.append(_bar(len(bars), open_, close, close - 0.15, open_ + 0.08))
    bars.append(_bar(len(bars), 109.55, 109.85, 109.4, 109.95))
    return bars


def _downtrend_rebound_bars() -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for cycle in range(14):
        base = 150.0 - cycle * 0.8
        leg = [
            (base, base - 0.55, base - 0.65, base),
            (base - 0.55, base - 0.80, base - 0.95, base - 0.45),
            (base - 0.80, base - 0.30, base - 0.85, base - 0.05),
            (base - 0.30, base - 0.75, base - 0.85, base - 0.15),
        ]
        for open_, close, low, high in leg:
            bars.append(_bar(len(bars), open_, close, low, high))
    rebound = [139.4, 139.95, 140.5, 140.85]
    for offset, close in enumerate(rebound):
        open_ = 139.25 if offset == 0 else rebound[offset - 1]
        bars.append(_bar(len(bars), open_, close, open_ - 0.08, close + 0.15))
    bars.append(_bar(len(bars), 140.85, 140.55, 140.45, 141.0))
    return bars


def _range_bars() -> list[dict[str, object]]:
    closes = [100, 101, 102, 101, 100, 99, 100, 101, 102, 101] * 6
    return [
        _bar(index, close - 0.3, close, close - 0.7, close + 0.7)
        for index, close in enumerate(closes[:70])
    ]


def test_uptrend_pullback_support_can_long():
    result = analyze_price_action(_uptrend_pullback_bars(), symbol="TEST", timeframe="1h")
    decision = result["decision"]
    assert decision["action"] == "LONG"
    assert decision["entry"] is not None
    assert decision["stop"] is not None
    assert decision["target"] is not None
    assert decision["stop"] < decision["entry"] < decision["target"]
    assert decision["rr"] >= 1.5
    assert result["simulation_only"] is True
    assert result["meta"]["engine"] == "xq_pa_local_v1"


def test_downtrend_rebound_resistance_can_short():
    result = analyze_price_action(_downtrend_rebound_bars(), symbol="TEST", timeframe="1h")
    decision = result["decision"]
    assert decision["action"] == "SHORT"
    assert decision["target"] < decision["entry"] < decision["stop"]
    assert decision["rr"] >= 1.5
    assert result["market_context"]["direction"] == "bearish"


def test_range_middle_waits():
    result = analyze_price_action(_range_bars(), symbol="TEST")
    assert result["decision"]["action"] == "WAIT"
    assert "middle_of_range" in result["decision"]["reason_codes"]
    assert result["market_context"]["cycle_position"] == "trading_range"


def test_low_rr_waits_but_keeps_structural_reject_evidence():
    result = analyze_price_action(_uptrend_pullback_bars(), min_rr=10.0)
    assert result["decision"]["action"] == "WAIT"
    assert "rr_below_minimum" in result["decision"]["reason_codes"]
    assert result["decision"]["rr"] is not None
    assert result["decision"]["rr"] < 10.0


def test_requires_sixty_bars():
    with pytest.raises(ValueError, match="数据不足：至少需要 60 根K线"):
        analyze_price_action(_range_bars()[:59])


def test_result_is_deterministic():
    bars = _uptrend_pullback_bars()
    first = analyze_price_action(bars, symbol="TEST")
    second = analyze_price_action(list(bars), symbol="TEST")
    assert first == second


def test_output_prices_are_structurally_valid():
    up = analyze_price_action(_uptrend_pullback_bars())
    down = analyze_price_action(_downtrend_rebound_bars())
    assert math.isfinite(up["decision"]["entry"])
    assert math.isfinite(up["decision"]["stop"])
    assert math.isfinite(up["decision"]["target"])
    assert math.isfinite(down["decision"]["entry"])
    assert math.isfinite(down["decision"]["stop"])
    assert math.isfinite(down["decision"]["target"])
    assert up["decision"]["stop"] < up["decision"]["entry"] < up["decision"]["target"]
    assert down["decision"]["target"] < down["decision"]["entry"] < down["decision"]["stop"]

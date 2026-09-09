from __future__ import annotations

import math

import pytest

from xquant.analysis.sr_levels import detect_support_resistance


def _bar(index: int, close: float, *, spread: float = 0.5, volume: int = 1000) -> dict[str, object]:
    open_price = close - spread * 0.4
    return {
        "session_id": f"S{index:04d}",
        "open": open_price,
        "high": close + spread * 0.5,
        "low": open_price - spread * 0.5,
        "close": close,
        "volume": volume,
    }


def _uptrend_bars(count: int = 120) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    close = 100.0
    for index in range(count):
        close += 0.15 + (0.05 if index % 7 == 0 else -0.03)
        bars.append(_bar(index, close, spread=0.7, volume=900 + (index % 20) * 10))
    return bars


def _oscillation_bars(count: int = 120) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for index in range(count):
        close = 100.0 + (2.5 if index % 10 < 5 else -2.5) + (0.05 if index % 2 else -0.05)
        bars.append(_bar(index, close, spread=1.0, volume=1200 - (index % 10) * 30))
    return bars


def test_uptrend_and_oscillation_emit_reasonable_levels() -> None:
    for bars in (_uptrend_bars(), _oscillation_bars()):
        result = detect_support_resistance(bars, symbol="600000", timeframe="1d")

        assert result["bars_used"] == 120
        assert result["levels"]
        assert set(result["trend"]) == {"label", "detail"}
        assert result["trend"]["label"] in {"上涨", "下跌", "震荡"}
        for level in result["levels"]:
            assert level["zone_type"] in {"support", "resistance"}
            assert level["low"] < level["center"] < level["high"]
            assert 0.3 <= level["width_atr"] <= 1.2
            assert 0.5 <= level["distance_atr"] <= 5.0
            assert level["edge_score"] >= 0.0
            assert level["tf_count"] >= 1


def test_direction_filter_and_side_limit() -> None:
    bars = _oscillation_bars()
    long_result = detect_support_resistance(bars, direction="long", n_zones=6)
    short_result = detect_support_resistance(bars, direction="short", n_zones=6)
    both_result = detect_support_resistance(bars, direction="both", n_zones=6)

    assert all(level["zone_type"] == "support" for level in long_result["levels"])
    assert all(level["center"] < long_result["current_price"] for level in long_result["levels"])
    assert all(level["zone_type"] == "resistance" for level in short_result["levels"])
    assert all(level["center"] > short_result["current_price"] for level in short_result["levels"])
    assert len(both_result["levels"]) <= 6
    assert all(level["tf_count"] == 1 for level in both_result["levels"])
    assert both_result["meta"]["direction"] == "both"


def test_invalid_direction_falls_back_to_both() -> None:
    result = detect_support_resistance(_oscillation_bars(), direction="invalid")

    assert result["meta"]["direction"] == "both"
    assert {level["zone_type"] for level in result["levels"]} <= {"support", "resistance"}


def test_insufficient_data_raises_exact_value_error() -> None:
    with pytest.raises(ValueError, match="数据不足：至少需要 60 根K线"):
        detect_support_resistance(_uptrend_bars(59))


def test_results_are_deterministic() -> None:
    bars = _oscillation_bars()
    first = detect_support_resistance(bars, symbol="SZ000001")
    second = detect_support_resistance(bars, symbol="SZ000001")

    assert first == second


def test_numeric_strings_aliased_fields_and_metric_bounds() -> None:
    bars = _uptrend_bars()
    adapted: list[dict[str, object]] = []
    for index, bar in enumerate(bars):
        adapted.append(
            {
                "session": bar["session_id"],
                "o": str(bar["open"]),
                "h": str(bar["high"]),
                "l": str(bar["low"]),
                "c": str(bar["close"]),
                "vol": str(bar["volume"]),
            }
        )

    result = detect_support_resistance(adapted, lookback=100, n_zones=6)
    current_price = result["current_price"]
    assert result["bars_used"] == 100
    assert result["atr"] > 0.0
    assert math.isclose(result["atr_pct"], result["atr"] / current_price * 100.0, rel_tol=1e-6)
    for level in result["levels"]:
        expected_distance = (level["center"] - current_price) / current_price * 100.0
        assert math.isclose(level["distance_pct"], expected_distance, rel_tol=2e-5, abs_tol=2e-5)
        expected_atr_distance = abs(level["center"] - current_price) / result["atr"]
        assert math.isclose(level["distance_atr"], expected_atr_distance, rel_tol=2e-5, abs_tol=2e-5)


def test_summary_risk_reward_has_quality_and_fallback() -> None:
    bars = _uptrend_bars()
    result = detect_support_resistance(bars, direction="long")
    risk_reward = result["summary"]["risk_reward"]

    assert risk_reward["quality"] in {"优秀", "良好", "一般", "较差"}
    assert risk_reward["potential_reward_atr"] > 0.0
    assert risk_reward["potential_risk_atr"] > 0.0
    assert risk_reward["basis"] in {"nearest_levels", "fallback"}
    assert result["summary"]["caveat"]

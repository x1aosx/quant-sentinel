from __future__ import annotations

import math

import pytest

from xquant.analysis.sr_levels import (
    _build_price_profile,
    _touch_event_stats,
    detect_support_resistance,
)


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


def test_v3_fields_evidence_and_null_probabilities() -> None:
    result = detect_support_resistance(_oscillation_bars())
    required_fields = {
        "zone_label",
        "width_pct",
        "stale",
        "p_stall",
        "stall_bucket",
        "vp_strength",
        "bucket",
        "bucket_hold_rate",
        "bucket_fwd_ret",
        "bucket_n",
        "p_touch",
        "p_hold",
        "p_effective",
        "n_events",
        "n_decided",
        "n_hold",
    }

    assert result["levels"]
    for level in result["levels"]:
        assert required_fields <= set(level)
        assert level["zone_label"] == (
            "支撑带" if level["zone_type"] == "support" else "压力带"
        )
        assert level["bucket"] in {"Q1", "Q2", "Q3", "Q4"}
        assert level["stall_bucket"] in {"Q1", "Q2", "Q3", "Q4"}
        assert 0.0 <= level["stale"] <= 1.0
        assert 0.0 <= level["p_stall"] <= 1.0
        assert 0.0 <= level["vp_strength"] <= 1.0
        assert level["p_touch"] is None
        assert level["p_hold"] is None
        assert level["p_effective"] is None
        assert level["bucket_hold_rate"] is None
        assert level["bucket_fwd_ret"] is None
        assert level["bucket_n"] is None
        assert level["n_hold"] <= level["n_decided"] <= level["n_events"]
        expected_width_pct = (
            (level["high"] - level["low"]) / result["current_price"] * 100.0
        )
        assert math.isclose(level["width_pct"], expected_width_pct, rel_tol=1e-4, abs_tol=1e-4)
        event_component = min(math.log1p(level["n_events"]) / math.log1p(12.0), 1.0)
        expected_edge = 100.0 * (0.65 * event_component + 0.35 * level["stale"])
        assert math.isclose(level["edge_score"], expected_edge, rel_tol=1e-3, abs_tol=0.01)

    summary = result["summary"]
    assert {
        "headline",
        "nearest",
        "best",
        "risk_reward",
        "caveat",
        "direction",
        "direction_label",
        "trend_label",
        "trend_detail",
    } <= set(summary)
    assert summary["direction_label"] in {"只做多", "只做空", "多空都做"}


def test_volume_profile_conserves_decayed_volume() -> None:
    bars = _oscillation_bars()
    highs = [float(bar["high"]) for bar in bars]
    lows = [float(bar["low"]) for bar in bars]
    closes = [float(bar["close"]) for bar in bars]
    volumes = [float(bar["volume"]) for bar in bars]
    expected_volume = sum(
        volume * 2.0 ** (-(len(volumes) - 1 - index) / 60.0)
        for index, volume in enumerate(volumes)
    )

    profile = _build_price_profile(highs, lows, closes, volumes, atr=2.0)

    assert math.isclose(sum(profile.volume_density), expected_volume, rel_tol=1e-10)
    assert all(0.0 <= value <= 1.0 for value in profile.vp_strength)
    assert profile.bin_width >= 0.5


def test_touch_events_deduplicate_and_validate_approach_direction() -> None:
    highs = [106.0, 103.0, 103.0, 103.0, 103.0, 103.0, 104.0]
    lows = [104.0, 98.0, 98.0, 98.0, 98.0, 98.0, 99.0]
    closes = [105.0, 101.0, 101.0, 101.0, 101.0, 101.0, 104.0]
    atr_values = [1.0] * len(closes)
    raw_touches = sum(high >= 99.0 and low <= 101.0 for high, low in zip(highs, lows, strict=True))

    support_stats = _touch_event_stats(
        highs,
        lows,
        closes,
        atr_values,
        low=99.0,
        high=101.0,
        zone_type="support",
        fallback_atr=1.0,
    )
    wrong_direction_stats = _touch_event_stats(
        highs,
        lows,
        closes,
        atr_values,
        low=99.0,
        high=101.0,
        zone_type="resistance",
        fallback_atr=1.0,
    )

    assert raw_touches > 1
    assert support_stats["n_events"] == 1
    assert support_stats["n_decided"] == 1
    assert support_stats["n_hold"] == 1
    assert support_stats["hold_rate"] == 1.0
    assert wrong_direction_stats["n_events"] == 0
    assert wrong_direction_stats["stale"] == 1.0


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


def test_invalid_numeric_values_raise() -> None:
    bars = _oscillation_bars()
    bars[0]["close"] = math.nan

    with pytest.raises(ValueError, match="K线数值无效"):
        detect_support_resistance(bars)


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

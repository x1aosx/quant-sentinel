from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from xquant.analysis.density_sr import (
    SREngine,
    frame_from_bars,
    get_calibration,
    get_prob_models,
    pick_mtf_timeframes,
)
from xquant.analysis.density_sr.data import atr_series
from xquant.analysis.density_sr.pivots import zigzag
from xquant.analysis.density_sr.profile import decay_weights, volume_profile
from xquant.analysis.sr_levels import detect_support_resistance


def _bar(index: int, close: float, *, spread: float = 0.5, volume: int = 1000) -> dict[str, Any]:
    open_price = close - spread * 0.4
    return {
        "session_id": f"S{index:04d}",
        "open": open_price,
        "high": close + spread * 0.5,
        "low": open_price - spread * 0.5,
        "close": close,
        "volume": volume,
    }


def _uptrend_bars(count: int = 120) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    close = 100.0
    for index in range(count):
        close += 0.15 + (0.05 if index % 7 == 0 else -0.03)
        bars.append(_bar(index, close, spread=0.7, volume=900 + (index % 20) * 10))
    return bars


def _oscillation_bars(count: int = 120) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
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
            assert 0.5 <= abs(level["distance_atr"]) <= 5.0
            assert level["edge_score"] >= 0.0
            assert level["tf_count"] >= 1


def test_probability_and_evidence_fields_are_consistent() -> None:
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
        "tfs",
        "tf_count",
    }

    assert result["levels"]
    assert result["meta"]["engine"] == "V3_fusion"
    assert result["meta"]["calibrated"] is True
    assert result["meta"]["prob_ready"] is True

    calibration = get_calibration()
    models = get_prob_models()
    assert calibration.ok and models.ok

    for level in result["levels"]:
        assert required_fields <= set(level)
        assert level["zone_label"] == ("支撑带" if level["zone_type"] == "support" else "压力带")
        assert level["bucket"] in {"Q1", "Q2", "Q3", "Q4"}
        assert level["stall_bucket"] in {"Q1", "Q2", "Q3", "Q4"}
        assert 0.0 <= level["stale"] <= 1.0
        # p_stall 与标定表同源的未归一化分数，不是概率
        assert math.isfinite(level["p_stall"])
        assert 0.0 <= level["vp_strength"] <= 1.0
        # 双概率：模型已随包分发，四条概率都必须有值且有界
        for key in ("p_touch", "p_hold"):
            assert level[key] is not None
            assert 0.0 < level[key] < 1.0
        assert level["p_effective"] is not None
        assert math.isclose(
            level["p_effective"],
            level["p_touch"] * level["p_hold"],
            rel_tol=1e-3,
            abs_tol=1e-3,
        )
        # 分位档实测守住率来自回测标定表，必须与档位同源
        stats = calibration.stats(level["zone_type"], level["bucket"])
        assert stats is not None
        assert math.isclose(level["bucket_hold_rate"], stats["hold_rate"], abs_tol=1e-6)
        assert level["bucket_n"] == stats["n_decided"]

        expected_width_pct = (level["high"] - level["low"]) / result["current_price"] * 100.0
        # width_pct 输出保留 2 位小数，比较时按同精度
        assert abs(level["width_pct"] - expected_width_pct) <= 0.01


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
    adapted: list[dict[str, Any]] = []
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
        assert (
            abs(level["distance_pct"] - (level["center"] - current_price) / current_price * 100.0)
            <= 0.01
        )
        expected_atr_distance = (level["center"] - current_price) / result["atr"]
        assert abs(level["distance_atr"] - expected_atr_distance) <= 0.01


def test_summary_risk_reward_has_quality_and_fallback() -> None:
    bars = _uptrend_bars()
    result = detect_support_resistance(bars, direction="long")
    risk_reward = result["summary"]["risk_reward"]

    assert risk_reward["quality"] in {"优秀", "良好", "一般", "较差"}
    assert risk_reward["potential_reward_atr"] > 0.0
    assert risk_reward["potential_risk_atr"] > 0.0
    assert risk_reward["basis"] in {"nearest_levels", "fallback"}
    assert result["summary"]["caveat"]
    assert result["summary"]["headline"]


def test_volume_profile_conserves_decayed_volume() -> None:
    bars = _oscillation_bars()
    highs = np.array([float(bar["high"]) for bar in bars])
    lows = np.array([float(bar["low"]) for bar in bars])
    volumes = np.array([float(bar["volume"]) for bar in bars])
    weights = volumes * decay_weights(len(bars), 60.0)

    profile = volume_profile(highs, lows, weights, p0=90.0, bin_w=0.5, n_bins=80)

    assert math.isclose(float(profile.sum()), float(weights.sum()), rel_tol=1e-9)
    assert (profile >= -1e-9).all()  # 浮点差分累加可能有极小负值


def test_zigzag_pivots_are_causal() -> None:
    bars = _oscillation_bars()
    highs = np.array([float(bar["high"]) for bar in bars])
    lows = np.array([float(bar["low"]) for bar in bars])
    closes = np.array([float(bar["close"]) for bar in bars])
    atr = atr_series(highs, lows, closes)

    pivots = zigzag(highs, lows, atr, k_atr=2.0)

    assert len(pivots.idx) > 0
    for index, price, confirm in zip(pivots.idx, pivots.price, pivots.confirm_idx, strict=True):
        # 枢轴价格必须来自历史 K 线，且确认时点不早于枢轴本身
        assert 0 <= index < len(bars)
        assert confirm >= index
        assert math.isclose(price, highs[index], rel_tol=1e-9) or math.isclose(
            price, lows[index], rel_tol=1e-9
        )


def test_mtf_frames_mark_resonance() -> None:
    bars = _oscillation_bars()
    result = detect_support_resistance(
        bars,
        timeframe="1d",
        mtf_frames=[("4h", bars)],
    )

    assert result["mtf_zones"]
    assert result["mtf_zones"][0]["tf"] == "4h"
    assert result["meta"]["mtf_timeframes"] == ["4h"]
    assert any(level["tf_count"] >= 2 for level in result["levels"])


def test_pick_mtf_timeframes_prefers_neighbours() -> None:
    picked = pick_mtf_timeframes("1d", ["1h", "4h", "1w", "5m"])

    assert "4h" in picked and "1w" in picked
    assert "1d" not in picked


def test_engine_detects_on_frame_from_bars() -> None:
    frame = frame_from_bars(_oscillation_bars(), symbol="TEST", timeframe="1d")
    zones, info = SREngine(n_zones=6).detect(frame, "TEST")

    assert info["engine"] == "V3_fusion"
    assert info["atr"] > 0.0
    assert zones
    assert all(zone["tf_count"] == 1 for zone in zones)

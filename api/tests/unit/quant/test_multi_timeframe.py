from __future__ import annotations

import pytest

from xquant.quant import (
    MultiTimeframeAnalyzer,
    MultiTimeframeProfile,
    MultiTimeframeResult,
    QuantDecisionEngine,
    SingleTimeframeAnalyzer,
    TimeframeAnalysisResult,
)


def _result(
    timeframe: str,
    trend: str,
    score: float,
    phase: str,
    *,
    confidence: float = 0.8,
) -> TimeframeAnalysisResult:
    return TimeframeAnalysisResult(
        symbol="TEST",
        timeframe=timeframe,
        timestamp=f"2026-09-16T10:{timeframe[:2].zfill(2)}:00+08:00",
        trend=trend,
        trend_score=score,
        phase=phase,
        momentum_score=score,
        volatility_score=45.0,
        support_levels=[95.0],
        resistance_levels=[105.0],
        volume_state="NORMAL",
        price_structure="HIGHER_HIGH_HIGHER_LOW",
        signals=["test"],
        confidence=confidence,
        raw_result={"meta": {"engine": "test"}},
    )


def _profile_results(
    strategic: tuple[str, float, str],
    tactical: tuple[str, float, str],
    confirmation: tuple[str, float, str],
    execution: tuple[str, float, str],
) -> dict[str, TimeframeAnalysisResult]:
    return {
        "1d": _result("1d", *strategic),
        "1h": _result("1h", *tactical),
        "30m": _result("30m", *confirmation),
        "15m": _result("15m", *execution),
    }


def _bars(count: int = 140) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    close = 100.0
    for index in range(count):
        close += 0.12 + (0.04 if index % 5 else -0.02)
        open_price = close - 0.08
        bars.append(
            {
                "timestamp": f"2026-09-16T{index // 4:02d}:{index % 4 * 15:02d}:00+08:00",
                "open": open_price,
                "high": close + 0.15,
                "low": open_price - 0.12,
                "close": close,
                "volume": 1000 + (index % 20) * 25,
            }
        )
    return bars


def test_case_1_all_timeframes_bullish() -> None:
    results = _profile_results(
        ("BULLISH", 82.0, "TRENDING"),
        ("STRONG_BULLISH", 88.0, "TRENDING"),
        ("BULLISH", 79.0, "TRENDING"),
        ("BULLISH", 76.0, "BREAKOUT"),
    )

    profile = MultiTimeframeAnalyzer().analyze("TEST", results, MultiTimeframeProfile())
    decision = QuantDecisionEngine().evaluate(profile, results)

    assert profile.alignment_score >= 80.0
    assert profile.conflict_score <= 20.0
    assert profile.bullish_score > profile.bearish_score
    assert profile.summary_state == "FULL_BULLISH_ALIGNMENT"
    assert decision.action == "BUY"


def test_case_2_large_up_small_cycles_pullback() -> None:
    results = _profile_results(
        ("BULLISH", 82.0, "TRENDING"),
        ("BEARISH", 40.0, "PULLBACK"),
        ("BEARISH", 38.0, "CONSOLIDATION"),
        ("BEARISH", 35.0, "TRENDING"),
    )

    profile = MultiTimeframeAnalyzer().analyze("TEST", results, MultiTimeframeProfile())
    decision = QuantDecisionEngine().evaluate(profile, results)

    assert profile.strategic_trend == "BULLISH"
    assert profile.intraday_state == "PULLBACK"
    assert profile.summary_state.startswith("BULLISH")
    assert "BEARISH" not in profile.summary_state
    assert decision.action != "BUY"


def test_case_3_strategy_up_with_execution_turning_up() -> None:
    results = _profile_results(
        ("BULLISH", 82.0, "TRENDING"),
        ("BEARISH", 43.0, "PULLBACK"),
        ("NEUTRAL", 52.0, "CONSOLIDATION"),
        ("BULLISH", 72.0, "BREAKOUT"),
    )

    profile = MultiTimeframeAnalyzer().analyze("TEST", results, MultiTimeframeProfile())
    decision = QuantDecisionEngine().evaluate(profile, results)

    assert profile.summary_state == "BULLISH_PULLBACK_TURNING_UP"
    assert decision.action in {"WAIT_BUY", "WATCH"}


def test_case_4_high_conflict_never_allows_buy() -> None:
    results = _profile_results(
        ("BULLISH", 82.0, "TRENDING"),
        ("BEARISH", 32.0, "TRENDING"),
        ("BEARISH", 28.0, "TRENDING"),
        ("BEARISH", 25.0, "TRENDING"),
    )

    profile = MultiTimeframeAnalyzer().analyze("TEST", results, MultiTimeframeProfile())
    decision = QuantDecisionEngine().evaluate(profile, results)

    assert profile.conflict_score >= 60.0
    assert profile.summary_state == "BULLISH_HIGH_CONFLICT"
    assert decision.action not in {"BUY", "WAIT_BUY"}


def test_case_5_missing_timeframes_are_reported() -> None:
    results = {
        "1d": _result("1d", "BULLISH", 82.0, "TRENDING"),
        "15m": _result("15m", "BULLISH", 72.0, "BREAKOUT"),
    }

    profile = MultiTimeframeAnalyzer().analyze("TEST", results, MultiTimeframeProfile())
    decision = QuantDecisionEngine().evaluate(profile, results)

    assert profile.missing_timeframes == ["1h", "30m"]
    assert profile.metadata["coverage_ratio"] == pytest.approx(0.5)
    assert profile.alignment_score < 80.0
    assert profile.metadata["confidence"] < 1.0
    assert "missing_data" in decision.risk_flags


def test_high_conflict_with_misleading_summary_still_blocks_buy() -> None:
    profile = MultiTimeframeResult(
        symbol="TEST",
        strategic_trend="BULLISH",
        strategic_score=95.0,
        alignment_score=90.0,
        conflict_score=75.0,
        bullish_score=90.0,
        bearish_score=70.0,
        summary_state="FULL_BULLISH_ALIGNMENT",
        metadata={"coverage_ratio": 1.0},
    )

    decision = QuantDecisionEngine().evaluate(profile)

    assert decision.action != "BUY"
    assert "no_buy_due_conflict" in decision.reason_codes


def test_single_timeframe_analyzer_reuses_existing_engines() -> None:
    result = SingleTimeframeAnalyzer().analyze("TEST", "1h", _bars())

    assert result.timeframe == "1h"
    assert result.trend in {
        "STRONG_BULLISH",
        "BULLISH",
        "NEUTRAL",
        "BEARISH",
        "STRONG_BEARISH",
    }
    assert 0.0 <= result.trend_score <= 100.0
    assert 0.0 <= result.momentum_score <= 100.0
    assert 0.0 <= result.volatility_score <= 100.0
    assert 0.0 <= result.confidence <= 1.0
    assert result.support_levels or result.resistance_levels
    assert "support_resistance" in result.raw_result
    assert "price_action" in result.raw_result
    assert result.raw_result["metadata"]["engine_version"] == "xq_quant_single_timeframe_v1"


def test_single_timeframe_insufficient_data_returns_low_confidence_result() -> None:
    result = SingleTimeframeAnalyzer().analyze("TEST", "15m", _bars(20))

    assert result.signals == ["insufficient_data"]
    assert result.confidence == 0.0
    assert result.trend == "UNKNOWN"


def test_profile_payload_and_result_serialization() -> None:
    profile = MultiTimeframeProfile.from_payload(
        {
            "strategic": {"timeframe": "4h"},
            "weights": {"strategic": 0.5, "tactical": 0.25},
        }
    )
    results = {
        "4h": _result("4h", "BULLISH", 80.0, "TRENDING"),
        "1h": _result("1h", "BULLISH", 72.0, "TRENDING"),
    }

    multi = MultiTimeframeAnalyzer().analyze("TEST", results, profile)

    assert profile.strategic == "4h"
    assert multi.missing_timeframes == ["30m", "15m"]
    assert multi.to_dict()["symbol"] == "TEST"
    assert multi.to_dict()["metadata"]["engine_version"] == "xq_multi_timeframe_v1"

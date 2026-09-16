from __future__ import annotations

import json
import math

from xquant.ai import context_builder
from xquant.ai.context_builder import (
    build_quant_analysis_context,
    build_quant_analysis_prompt,
    run_quant_analysis,
)
from xquant.ai.service import normalize_ai_settings


def _timeframe(
    timeframe: str,
    *,
    trend: str = "bullish",
    volume_state: str = "healthy",
    price_structure: str = "trend",
) -> dict:
    return {
        "symbol": "002277",
        "timeframe": timeframe,
        "trend": trend,
        "trend_score": 82,
        "phase": "trending",
        "momentum_score": 72,
        "volatility_score": 35,
        "support_levels": [5.2, 5.4],
        "resistance_levels": [5.9, 6.1],
        "volume_state": volume_state,
        "price_structure": price_structure,
        "signals": ["hold"],
        "confidence": 0.8,
    }


def _context() -> dict:
    timeframes = {
        "1D": _timeframe("1d"),
        "4H": _timeframe("4h", trend="pullback", volume_state="shrinking"),
        "2H": _timeframe("2h", trend="consolidation"),
        "5M": _timeframe("5m", trend="turning_bullish", volume_state="expanding"),
    }
    return build_quant_analysis_context(
        symbol="002277",
        name="友阿股份",
        quote={"current_price": 5.69, "change_pct": 1.2},
        timeframes=timeframes,
        multi_timeframe={
            "metadata": {
                "profile": {
                    "strategic": "1D",
                    "tactical": "4H",
                    "confirmation": "2H",
                    "execution": "5M",
                }
            },
            "summary_state": "bullish_pullback",
            "alignment_score": 72,
            "conflict_score": 18,
        },
        decision={
            "action": "WAIT_BUY",
            "confidence": 0.72,
            "trigger_conditions": ["15m 放量突破"],
            "invalid_conditions": ["1h 跌破支撑"],
            "risk_flags": ["等待确认"],
        },
        generated_at="2026-09-16T10:00:00+08:00",
    )


def test_context_uses_profile_roles_and_removes_raw_candles() -> None:
    timeframes = {
        "1d": {
            **_timeframe("1d"),
            "candles": [{"open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 100}],
            "nested": {"raw_bars": [{"close": 1.5}]},
            "series": [{"open": 1, "high": 2, "low": 0, "close": 1.5}],
        }
    }
    context = build_quant_analysis_context(
        symbol="002277",
        name="友阿股份",
        quote={"current_price": 5.69},
        timeframes=timeframes,
        multi_timeframe={"metadata": {"profile": {"strategic": "1D"}}},
        decision={"action": "WAIT"},
        generated_at="2026-09-16T10:00:00+08:00",
    )

    assert context["strategic"]["timeframe"] == "1d"
    assert context["strategic"]["trend"] == "bullish"
    assert context["tactical"]["timeframe"] == "1h"
    assert context["tactical"]["available"] is False
    assert context["volume_price"]["strategic"]["volume_state"] == "healthy"
    assert context["volume_price"]["strategic"]["price_structure"] == "trend"
    assert "candles" not in json.dumps(context)
    assert "raw_bars" not in json.dumps(context)
    assert '"open"' not in json.dumps(context)


def test_context_uses_default_profile_and_handles_missing_timeframes() -> None:
    timeframes = {
        "1d": _timeframe("1d"),
        "15m": _timeframe("15m", trend="turning_bullish"),
    }
    context = build_quant_analysis_context(
        symbol="002277",
        name=None,
        quote=None,
        timeframes=timeframes,
        multi_timeframe={},
        decision={},
        generated_at="2026-09-16T10:00:00+08:00",
    )

    assert {role: context[role]["timeframe"] for role in ("strategic", "tactical", "confirmation", "execution")} == {
        "strategic": "1d",
        "tactical": "1h",
        "confirmation": "30m",
        "execution": "15m",
    }
    assert context["strategic"]["available"] is True
    assert context["tactical"]["available"] is False
    assert context["confirmation"]["available"] is False
    assert context["execution"]["available"] is True


def test_context_cleans_nan_and_infinity_recursively() -> None:
    context = build_quant_analysis_context(
        symbol="002277",
        name="友阿股份",
        quote={"change_pct": math.nan},
        timeframes={
            "1d": {
                **_timeframe("1d"),
                "trend_score": math.inf,
                "nested": [{"value": -math.inf}, {"value": math.nan}],
            }
        },
        multi_timeframe={"alignment_score": math.nan},
        decision={"confidence": math.inf},
        generated_at="2026-09-16T10:00:00+08:00",
    )

    assert context["quote"]["change_pct"] is None
    assert context["strategic"]["trend_score"] is None
    assert context["strategic"]["nested"] == [{"value": None}, {"value": None}]
    assert context["multi_timeframe"]["alignment_score"] is None
    assert context["quant_decision"]["confidence"] is None
    json.dumps(context, allow_nan=False)


def test_quant_prompt_restricts_ai_to_explanation_and_fixed_json_fields() -> None:
    prompt = build_quant_analysis_prompt(_context())
    assert prompt[0]["role"] == "system"
    assert "不得重新计算趋势" in prompt[0]["content"]
    assert "不得给出真实投资建议" in prompt[0]["content"]
    assert prompt[1]["role"] == "user"
    for field in (
        "summary",
        "cycle_explanation",
        "scenarios",
        "risks",
        "trigger_conditions",
        "invalid_conditions",
        "notification_text",
    ):
        assert field in prompt[1]["content"]


def test_run_quant_analysis_uses_local_fallback_without_api_key() -> None:
    settings = normalize_ai_settings({"provider": {"api_key": ""}})
    result = run_quant_analysis(_context(), settings)

    assert result["status"] == "ok"
    assert result["source"] == "local"
    assert result["summary"]
    assert result["cycle_explanation"]
    assert result["trigger_conditions"] == ["15m 放量突破"]
    assert result["invalid_conditions"] == ["1h 跌破支撑"]
    assert set(result) == {
        "status",
        "source",
        "summary",
        "cycle_explanation",
        "scenarios",
        "risks",
        "trigger_conditions",
        "invalid_conditions",
        "notification_text",
        "generated_at",
        "raw_response",
    }


def test_run_quant_analysis_parses_model_json(monkeypatch) -> None:
    settings = normalize_ai_settings({"provider": {"api_key": "test-key"}})
    response = {
        "summary": "日线偏多，短周期等待确认。",
        "cycle_explanation": "1d 偏多，1h 回调。",
        "scenarios": [{"name": "基准", "description": "等待突破确认。"}],
        "risks": ["小周期转弱"],
        "trigger_conditions": ["放量突破"],
        "invalid_conditions": ["跌破支撑"],
        "notification_text": "等待确认。",
    }
    calls: list[tuple[object, list[dict[str, str]]]] = []

    def fake_call(provider, messages):
        calls.append((provider, messages))
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(context_builder, "call_chat_completion", fake_call)
    result = run_quant_analysis(_context(), settings)

    assert result["status"] == "ok"
    assert result["source"] == "model"
    assert result["summary"] == response["summary"]
    assert result["scenarios"] == response["scenarios"]
    assert result["raw_response"] == json.dumps(response, ensure_ascii=False)
    assert calls[0][0] is settings.provider
    assert calls[0][1][0]["role"] == "system"


def test_run_quant_analysis_returns_error_for_bad_json(monkeypatch) -> None:
    settings = normalize_ai_settings({"provider": {"api_key": "test-key"}})
    monkeypatch.setattr(
        context_builder,
        "call_chat_completion",
        lambda _provider, _messages: "not-json",
    )

    result = run_quant_analysis(_context(), settings)

    assert result["status"] == "error"
    assert result["source"] == "model"
    assert result["raw_response"] == "not-json"
    assert result["summary"].startswith("AI 分析失败")
    assert result["scenarios"] == []
    assert result["risks"] == []

"""Adapt existing deterministic analyzers into the unified quant contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from xquant.analysis.price_action import analyze_price_action
from xquant.analysis.sr_levels import detect_support_resistance

from ..core.models import TimeframeAnalysisResult

_SINGLE_TIMEFRAME_ENGINE = "xq_quant_single_timeframe_v1"
_INSUFFICIENT_DATA = "insufficient_data"
_INSUFFICIENT_DATA_MARKERS = ("insufficient_data", "数据不足")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_option(context: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(context.get(key, default))
    except (TypeError, ValueError):
        return default


def _timestamp(bars: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> str:
    for bar in reversed(bars):
        if not isinstance(bar, Mapping):
            continue
        for key in ("timestamp", "time", "datetime", "date", "session_id", "session"):
            value = bar.get(key)
            if value is not None and str(value).strip():
                return str(value)
    value = context.get("timestamp")
    return str(value) if value is not None else _now()


def _direction_from_sr(sr_result: Mapping[str, Any]) -> str:
    trend = sr_result.get("trend")
    label = trend.get("label") if isinstance(trend, Mapping) else trend
    normalized = str(label or "").strip().lower()
    if normalized in {"上涨", "up", "bullish", "uptrend"}:
        return "bullish"
    if normalized in {"下跌", "down", "bearish", "downtrend"}:
        return "bearish"
    return "neutral"


def _direction(sr_result: Mapping[str, Any], pa_result: Mapping[str, Any]) -> str:
    market_context = pa_result.get("market_context")
    if isinstance(market_context, Mapping):
        value = str(market_context.get("direction", "")).strip().lower()
        if value in {"bullish", "bearish", "neutral"}:
            return value
    return _direction_from_sr(sr_result)


def _trend_score(
    direction: str,
    sr_result: Mapping[str, Any],
    pa_result: Mapping[str, Any],
) -> float:
    market_context = pa_result.get("market_context")
    context = market_context if isinstance(market_context, Mapping) else {}
    score = 50.0
    score += _number(context.get("trading_score")) * 7.0
    score += _number(context.get("background_score")) * 5.0
    score += _number(context.get("recent_score")) * 3.0

    sr_direction = _direction_from_sr(sr_result)
    if direction == "bullish":
        score = max(score, 55.0)
        if sr_direction == "bullish":
            score += 5.0
    elif direction == "bearish":
        score = min(score, 45.0)
        if sr_direction == "bearish":
            score -= 5.0
    else:
        score = 50.0 + (score - 50.0) * 0.35
    return round(_clamp(score), 4)


def _trend_label(direction: str, score: float) -> str:
    if direction == "bullish":
        return "STRONG_BULLISH" if score >= 75.0 else "BULLISH"
    if direction == "bearish":
        return "STRONG_BEARISH" if score <= 25.0 else "BEARISH"
    return "NEUTRAL"


def _phase(pa_result: Mapping[str, Any]) -> str:
    market_context = pa_result.get("market_context")
    context = market_context if isinstance(market_context, Mapping) else {}
    cycle_position = str(context.get("cycle_position", "")).strip().lower()
    mapping = {
        "trending": "TRENDING",
        "trend_pullback": "PULLBACK",
        "trend_rebound": "PULLBACK",
        "trading_range": "CONSOLIDATION",
        "range_boundary": "CONSOLIDATION",
        "breakout": "BREAKOUT",
        "breakdown": "BREAKOUT",
        "breakout_attempt": "BREAKOUT",
    }
    return mapping.get(cycle_position, "UNKNOWN")


def _momentum_score(
    direction: str,
    pa_result: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
) -> float:
    market_context = pa_result.get("market_context")
    context = market_context if isinstance(market_context, Mapping) else {}
    closes = [_number(bar.get("close"), default=float("nan")) for bar in bars]
    recent_return = 0.0
    if len(closes) >= 6 and closes[-6] > 0.0:
        recent_return = (closes[-1] / closes[-6] - 1.0) * 100.0
    score = (
        50.0
        + _number(context.get("recent_score")) * 6.0
        + _number(context.get("trading_score")) * 5.0
        + recent_return * 4.0
    )
    if direction == "bullish":
        score += 8.0
    elif direction == "bearish":
        score -= 8.0
    return round(_clamp(score), 4)


def _volatility_score(
    sr_result: Mapping[str, Any],
    pa_result: Mapping[str, Any],
) -> float:
    atr_pct = _number(pa_result.get("atr_pct"), default=_number(sr_result.get("atr_pct")))
    return round(_clamp(25.0 + atr_pct * 15.0), 4)


def _deduplicated(values: Sequence[float], limit: int = 5) -> list[float]:
    result: list[float] = []
    for value in values:
        rounded = round(float(value), 6)
        if any(abs(rounded - existing) <= max(abs(existing), 1.0) * 1e-8 for existing in result):
            continue
        result.append(rounded)
        if len(result) >= limit:
            break
    return result


def _levels(
    sr_result: Mapping[str, Any],
    pa_result: Mapping[str, Any],
) -> tuple[list[float], list[float]]:
    supports: list[float] = []
    resistances: list[float] = []
    raw_levels = sr_result.get("levels")
    if isinstance(raw_levels, Sequence):
        for level in raw_levels:
            if not isinstance(level, Mapping) or level.get("center") is None:
                continue
            center = _number(level.get("center"))
            if str(level.get("zone_type", "")).lower() == "support":
                supports.append(center)
            elif str(level.get("zone_type", "")).lower() == "resistance":
                resistances.append(center)

    features = pa_result.get("features")
    feature_map = features if isinstance(features, Mapping) else {}
    if not supports:
        supports = [_number(value) for value in feature_map.get("supports", [])]
    if not resistances:
        resistances = [_number(value) for value in feature_map.get("resistances", [])]

    return _deduplicated(sorted(supports, reverse=True)), _deduplicated(sorted(resistances))


def _volume_state(bars: Sequence[Mapping[str, Any]]) -> str:
    volumes = [_number(bar.get("volume", bar.get("vol", 0.0))) for bar in bars]
    if len(volumes) < 25:
        return "UNKNOWN"
    recent = volumes[-5:]
    baseline = volumes[-25:-5]
    recent_average = sum(recent) / len(recent)
    baseline_average = sum(baseline) / len(baseline) if baseline else 0.0
    if baseline_average <= 0.0:
        return "UNKNOWN"
    ratio = recent_average / baseline_average
    if ratio >= 1.3:
        return "EXPANDING"
    if ratio <= 0.7:
        return "SHRINKING"
    return "NORMAL"


def _price_structure(pa_result: Mapping[str, Any]) -> str:
    features = pa_result.get("features")
    feature_map = features if isinstance(features, Mapping) else {}
    breakout_quality = str(feature_map.get("breakout_quality", "none")).lower()
    events = feature_map.get("breakout_events")
    latest_direction = ""
    if isinstance(events, Sequence) and events:
        latest = events[-1]
        if isinstance(latest, Mapping):
            latest_direction = str(latest.get("direction", "")).lower()
    if breakout_quality == "surviving":
        if latest_direction == "down":
            return "BREAKDOWN"
        return "BREAKOUT"
    if breakout_quality in {"failed", "wick_probe"}:
        return "FAILED_BREAKOUT"

    swing_structure = str(feature_map.get("swing_structure", "insufficient")).lower()
    if swing_structure == "hh+hl":
        return "HIGHER_HIGH_HIGHER_LOW"
    if swing_structure == "ll+lh":
        return "LOWER_LOW_LOWER_HIGH"
    if swing_structure == "mixed":
        return "MIXED"
    return "RANGE"


def _signals(
    sr_result: Mapping[str, Any],
    pa_result: Mapping[str, Any],
    volume_state: str,
) -> list[str]:
    result: list[str] = []
    features = pa_result.get("features")
    feature_map = features if isinstance(features, Mapping) else {}
    for value in feature_map.get("patterns", []):
        text = str(value).strip().lower()
        if text and text not in result:
            result.append(text)
    decision = pa_result.get("decision")
    decision_map = decision if isinstance(decision, Mapping) else {}
    action = str(decision_map.get("action", "")).strip().lower()
    if action:
        result.append(f"price_action_{action}")
    if volume_state != "UNKNOWN":
        result.append(f"volume_{volume_state.lower()}")
    sr_direction = _direction_from_sr(sr_result)
    if sr_direction != "neutral":
        result.append(f"sr_trend_{sr_direction}")
    return list(dict.fromkeys(result))


def _confidence(
    sr_result: Mapping[str, Any],
    pa_result: Mapping[str, Any],
    direction: str,
    levels: tuple[list[float], list[float]],
    errors: Sequence[str],
) -> float:
    pa_decision = pa_result.get("decision")
    decision_map = pa_decision if isinstance(pa_decision, Mapping) else {}
    pa_confidence = _clamp(_number(decision_map.get("confidence"))) / 100.0
    level_score = min((len(levels[0]) + len(levels[1])) / 4.0, 1.0)
    sr_alignment = 1.0 if _direction_from_sr(sr_result) == direction else 0.5
    market_context = pa_result.get("market_context")
    context = market_context if isinstance(market_context, Mapping) else {}
    background = str(context.get("background_direction", "")).strip().lower()
    background_alignment = (
        1.0 if background == direction else 0.6 if background == "neutral" else 0.3
    )
    confidence = (
        pa_confidence * 0.35
        + level_score * 0.2
        + sr_alignment * 0.2
        + background_alignment * 0.15
        + 0.1
    )
    if errors:
        confidence *= 0.35
    return round(_clamp(confidence, 0.0, 1.0), 4)


def _empty_result(
    symbol: str,
    timeframe: str,
    timestamp: str,
    error: str,
) -> TimeframeAnalysisResult:
    return TimeframeAnalysisResult(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        trend="UNKNOWN",
        trend_score=50.0,
        phase="UNKNOWN",
        momentum_score=50.0,
        volatility_score=50.0,
        volume_state="UNKNOWN",
        price_structure="UNKNOWN",
        signals=[_INSUFFICIENT_DATA],
        confidence=0.0,
        raw_result={
            "errors": [error],
            "metadata": {
                "engine_version": _SINGLE_TIMEFRAME_ENGINE,
                "generated_at": _now(),
            },
        },
    )


def _is_insufficient_data(exc: ValueError) -> bool:
    message = str(exc)
    return any(marker in message for marker in _INSUFFICIENT_DATA_MARKERS)


class SingleTimeframeAnalyzer:
    """Combine support/resistance and price-action results for one timeframe."""

    def analyze(
        self,
        symbol: str,
        timeframe: str,
        bars: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> TimeframeAnalysisResult:
        options = dict(context or {})
        normalized_timeframe = str(timeframe).strip().lower()
        timestamp = _timestamp(bars, options)
        lookback = max(60, _int_option(options, "lookback", 120))
        sr_lookback = max(60, _int_option(options, "sr_lookback", lookback))
        n_zones = max(1, min(12, _int_option(options, "n_zones", 6)))

        errors: list[str] = []
        sr_result: dict[str, Any] = {}
        pa_result: dict[str, Any] = {}
        try:
            sr_result = detect_support_resistance(
                bars,
                symbol=symbol,
                timeframe=normalized_timeframe,
                lookback=sr_lookback,
                n_zones=n_zones,
                direction="both",
            )
        except ValueError as exc:
            if not _is_insufficient_data(exc):
                raise
            errors.append(str(exc))
        try:
            pa_result = analyze_price_action(
                bars,
                symbol=symbol,
                timeframe=normalized_timeframe,
                lookback=lookback,
                risk_fraction=_number(options.get("risk_fraction"), default=0.01),
                min_rr=_number(options.get("min_rr"), default=1.5),
                stance=str(options.get("stance", "conservative")),
            )
        except ValueError as exc:
            if not _is_insufficient_data(exc):
                raise
            errors.append(str(exc))

        if not sr_result and not pa_result:
            message = "; ".join(errors) or _INSUFFICIENT_DATA
            return _empty_result(symbol, normalized_timeframe, timestamp, message)

        direction = _direction(sr_result, pa_result)
        trend_score = _trend_score(direction, sr_result, pa_result)
        levels = _levels(sr_result, pa_result)
        volume_state = _volume_state(bars)
        confidence = _confidence(sr_result, pa_result, direction, levels, errors)

        return TimeframeAnalysisResult(
            symbol=symbol,
            timeframe=normalized_timeframe,
            timestamp=timestamp,
            trend=_trend_label(direction, trend_score),
            trend_score=trend_score,
            phase=_phase(pa_result),
            momentum_score=_momentum_score(direction, pa_result, bars),
            volatility_score=_volatility_score(sr_result, pa_result),
            support_levels=levels[0],
            resistance_levels=levels[1],
            volume_state=volume_state,
            price_structure=_price_structure(pa_result),
            signals=_signals(sr_result, pa_result, volume_state),
            confidence=confidence,
            raw_result={
                "support_resistance": sr_result,
                "price_action": pa_result,
                "errors": errors,
                "metadata": {
                    "engine_version": _SINGLE_TIMEFRAME_ENGINE,
                    "generated_at": _now(),
                    "algorithm_versions": {
                        "support_resistance": (
                            sr_result.get("meta", {}).get("engine")
                            if isinstance(sr_result.get("meta"), Mapping)
                            else None
                        ),
                        "price_action": (
                            pa_result.get("meta", {}).get("engine")
                            if isinstance(pa_result.get("meta"), Mapping)
                            else None
                        ),
                    },
                    "context": options,
                },
            },
        )


__all__ = ["SingleTimeframeAnalyzer"]

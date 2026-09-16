"""Fuse per-timeframe analysis without allowing small cycles to replace strategy."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ..core.models import (
    DEFAULT_ROLE_WEIGHTS,
    ROLE_NAMES,
    MultiTimeframeProfile,
    MultiTimeframeResult,
    TimeframeAnalysisResult,
)

_MULTI_TIMEFRAME_ENGINE = "xq_multi_timeframe_v1"
_PROFILE_VERSION = "mtf_profile_v1"
_SINGLE_TIMEFRAME_VERSION = "xq_quant_single_timeframe_v1"
_HIGH_CONFLICT_THRESHOLD = 60.0
_PULLBACK_PHASES = {"PULLBACK", "CONSOLIDATION"}
_TIMEFRAME_ALIASES = {
    "daily": "1d",
    "day": "1d",
    "d": "1d",
    "hourly": "1h",
    "hour": "1h",
    "60m": "1h",
    "h": "1h",
    "30min": "30m",
    "30": "30m",
    "15min": "15m",
    "15": "15m",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _normalize_timeframe(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _TIMEFRAME_ALIASES.get(text, text)


def _direction(value: Any) -> int:
    text = str(value or "").strip().upper()
    if "BULL" in text or text in {"UP", "LONG"}:
        return 1
    if "BEAR" in text or text in {"DOWN", "SHORT"}:
        return -1
    return 0


def _strength(result: TimeframeAnalysisResult) -> float:
    if _direction(result.trend) == 0:
        return 0.0
    return max(0.2, min(1.0, abs(result.trend_score - 50.0) / 50.0))


def _state(result: TimeframeAnalysisResult | None) -> str:
    if result is None:
        return "UNKNOWN"
    phase = str(result.phase or "").strip().upper()
    if phase and phase != "UNKNOWN":
        return phase
    return str(result.trend or "UNKNOWN").strip().upper()


def _result_from_value(value: Any) -> TimeframeAnalysisResult:
    if isinstance(value, TimeframeAnalysisResult):
        return value
    if isinstance(value, Mapping):
        return TimeframeAnalysisResult.from_dict(value)
    raise TypeError("timeframe result must be TimeframeAnalysisResult or mapping")


def _normalize_results(
    results: Mapping[str, Any],
) -> dict[str, TimeframeAnalysisResult]:
    normalized: dict[str, TimeframeAnalysisResult] = {}
    for key, value in results.items():
        if value is None:
            continue
        result = _result_from_value(value)
        timeframe = result.timeframe or str(key)
        if str(key).strip().lower() in ROLE_NAMES and result.timeframe:
            timeframe = result.timeframe
        normalized[_normalize_timeframe(timeframe)] = result
    return normalized


def _effective_weights(profile: MultiTimeframeProfile) -> dict[str, float]:
    weights: dict[str, float] = {}
    for role in ROLE_NAMES:
        try:
            value = float(profile.weights.get(role, DEFAULT_ROLE_WEIGHTS[role]))
        except (TypeError, ValueError):
            value = DEFAULT_ROLE_WEIGHTS[role]
        weights[role] = max(0.0, value)
    return weights


def _summary_state(
    strategic_sign: int,
    strategic_available: bool,
    tactical: TimeframeAnalysisResult | None,
    confirmation: TimeframeAnalysisResult | None,
    execution: TimeframeAnalysisResult | None,
    conflict_score: float,
) -> str:
    if not strategic_available:
        return "STRATEGIC_DATA_MISSING"
    if conflict_score >= _HIGH_CONFLICT_THRESHOLD:
        if strategic_sign > 0:
            return "BULLISH_HIGH_CONFLICT"
        if strategic_sign < 0:
            return "BEARISH_HIGH_CONFLICT"
        return "NEUTRAL_HIGH_CONFLICT"
    if strategic_sign == 0:
        return "NEUTRAL_MIXED"

    tactical_phase = str(tactical.phase if tactical else "").strip().upper()
    confirmation_phase = str(confirmation.phase if confirmation else "").strip().upper()
    tactical_sign = _direction(tactical.trend if tactical else "")
    confirmation_sign = _direction(confirmation.trend if confirmation else "")
    execution_sign = _direction(execution.trend if execution else "")

    if strategic_sign > 0:
        if tactical_phase == "PULLBACK" and execution_sign > 0:
            return "BULLISH_PULLBACK_TURNING_UP"
        if tactical_sign < 0 or confirmation_sign < 0:
            return "BULLISH_PULLBACK"
        if confirmation_phase == "CONSOLIDATION":
            return "BULLISH_CONSOLIDATING"
        if all(
            result is None or _direction(result.trend) >= 0
            for result in (tactical, confirmation, execution)
        ):
            return "FULL_BULLISH_ALIGNMENT"
        return "BULLISH_MIXED"

    if tactical_phase == "PULLBACK" and execution_sign < 0:
        return "BEARISH_REBOUND_TURNING_DOWN"
    if tactical_sign > 0 or confirmation_sign > 0:
        return "BEARISH_REBOUND"
    if confirmation_phase == "CONSOLIDATION":
        return "BEARISH_CONSOLIDATING"
    if all(
        result is None or _direction(result.trend) <= 0
        for result in (tactical, confirmation, execution)
    ):
        return "FULL_BEARISH_ALIGNMENT"
    return "BEARISH_MIXED"


class MultiTimeframeAnalyzer:
    """Build a weighted profile from strategic through execution timeframes."""

    def analyze(
        self,
        symbol: str,
        results: Mapping[str, Any],
        profile: MultiTimeframeProfile | Mapping[str, Any] | None = None,
    ) -> MultiTimeframeResult:
        active_profile = (
            profile
            if isinstance(profile, MultiTimeframeProfile)
            else MultiTimeframeProfile.from_payload(profile)
            if profile
            else MultiTimeframeProfile()
        )
        normalized_results = _normalize_results(results)
        weights = _effective_weights(active_profile)
        role_timeframes = active_profile.role_timeframes()
        role_results: dict[str, TimeframeAnalysisResult] = {}
        missing: list[str] = []

        for role in ROLE_NAMES:
            timeframe = _normalize_timeframe(role_timeframes[role])
            result = normalized_results.get(timeframe)
            if result is None:
                missing.append(role_timeframes[role])
            else:
                role_results[role] = result

        available_weight = sum(weights[role] for role in role_results)
        total_weight = sum(weights.values())
        coverage_ratio = available_weight / total_weight if total_weight > 0.0 else 0.0

        strategic = role_results.get("strategic")
        tactical = role_results.get("tactical")
        confirmation = role_results.get("confirmation")
        execution = role_results.get("execution")
        strategic_sign = _direction(strategic.trend if strategic else "")

        bullish_weight = 0.0
        bearish_weight = 0.0
        raw_conflict = 0.0
        for role, result in role_results.items():
            sign = _direction(result.trend)
            weighted_strength = weights[role] * _strength(result)
            if sign > 0:
                bullish_weight += weighted_strength
            elif sign < 0:
                bearish_weight += weighted_strength

            if strategic_sign == 0 or sign == 0 or sign == strategic_sign:
                continue
            phase = str(result.phase or "").strip().upper()
            suppression = 0.45 if role != "strategic" and phase in _PULLBACK_PHASES else 1.0
            raw_conflict += weights[role] * suppression

        if available_weight > 0.0:
            bullish_score = _clamp(bullish_weight / available_weight * 100.0)
            bearish_score = _clamp(bearish_weight / available_weight * 100.0)
            raw_conflict_score = _clamp(raw_conflict / available_weight * 100.0)
        else:
            bullish_score = bearish_score = raw_conflict_score = 0.0

        conflict_score = round(raw_conflict_score * coverage_ratio, 4)
        alignment_score = round(max(0.0, 100.0 - raw_conflict_score) * coverage_ratio, 4)
        if not strategic:
            conflict_score = round(conflict_score * 0.8, 4)
            alignment_score = round(alignment_score * 0.7, 4)

        summary_state = _summary_state(
            strategic_sign,
            strategic is not None,
            tactical,
            confirmation,
            execution,
            conflict_score,
        )
        if conflict_score >= _HIGH_CONFLICT_THRESHOLD:
            alignment_state = "HIGH_CONFLICT"
        elif alignment_score >= 80.0:
            alignment_state = "FULL_ALIGNMENT"
        elif alignment_score >= 55.0:
            alignment_state = "PARTIAL_ALIGNMENT"
        else:
            alignment_state = "MIXED"

        available_results = [role_results[role] for role in ROLE_NAMES if role in role_results]
        timestamps = [
            result.timestamp
            for result in available_results
            if str(result.timestamp).strip()
        ]
        timestamp = max(timestamps) if timestamps else _now()
        confidence = _clamp(
            coverage_ratio * (alignment_score / 100.0) * (1.0 - conflict_score / 200.0),
            0.0,
            1.0,
        )

        return MultiTimeframeResult(
            symbol=symbol,
            timestamp=timestamp,
            strategic_trend=strategic.trend if strategic else "UNKNOWN",
            strategic_score=strategic.trend_score if strategic else 0.0,
            intraday_state=_state(tactical),
            confirmation_state=_state(confirmation),
            execution_state=_state(execution),
            alignment_score=alignment_score,
            conflict_score=conflict_score,
            bullish_score=round(bullish_score, 4),
            bearish_score=round(bearish_score, 4),
            summary_state=summary_state,
            missing_timeframes=missing,
            metadata={
                "engine_version": _MULTI_TIMEFRAME_ENGINE,
                "profile_version": _PROFILE_VERSION,
                "strategy_version": _SINGLE_TIMEFRAME_VERSION,
                "generated_at": _now(),
                "profile": active_profile.to_dict(),
                "role_timeframes": dict(role_timeframes),
                "weights": weights,
                "available_timeframes": [
                    role_timeframes[role] for role in ROLE_NAMES if role in role_results
                ],
                "missing_timeframes": list(missing),
                "coverage_ratio": round(coverage_ratio, 4),
                "alignment_state": alignment_state,
                "confidence": round(confidence, 4),
                "source_timestamps": {
                    role: role_results[role].timestamp
                    for role in ROLE_NAMES
                    if role in role_results
                },
            },
        )

__all__ = ["MultiTimeframeAnalyzer"]

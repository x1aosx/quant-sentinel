"""Rule-based decision engine that consumes multi-timeframe quant output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.models import MultiTimeframeResult, QuantDecision, TimeframeAnalysisResult

_ENGINE_VERSION = "xq_quant_decision_v1"
_HIGH_CONFLICT_THRESHOLD = 60.0
_ROLE_ORDER = ("strategic", "tactical", "confirmation", "execution")


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _direction(value: Any) -> int:
    text = str(value or "").strip().upper()
    if "BULL" in text or text in {"UP", "LONG"}:
        return 1
    if "BEAR" in text or text in {"DOWN", "SHORT"}:
        return -1
    return 0


def _normalize_timeframe(value: Any) -> str:
    return str(value or "").strip().lower()


def _as_result(value: Any) -> TimeframeAnalysisResult | None:
    if isinstance(value, TimeframeAnalysisResult):
        return value
    if isinstance(value, Mapping):
        return TimeframeAnalysisResult.from_dict(value)
    return None


def _role_mapping(multi_timeframe: MultiTimeframeResult) -> dict[str, str]:
    profile = multi_timeframe.metadata.get("profile")
    if not isinstance(profile, Mapping):
        return {
            "strategic": "1d",
            "tactical": "1h",
            "confirmation": "30m",
            "execution": "15m",
        }
    return {
        role: _normalize_timeframe(profile.get(role, default))
        for role, default in (
            ("strategic", "1d"),
            ("tactical", "1h"),
            ("confirmation", "30m"),
            ("execution", "15m"),
        )
    }


def _role_results(
    timeframe_results: Mapping[str, Any] | None,
    multi_timeframe: MultiTimeframeResult,
) -> dict[str, TimeframeAnalysisResult]:
    if not timeframe_results:
        return {}
    normalized = {
        _normalize_timeframe(key): result
        for key, value in timeframe_results.items()
        if (result := _as_result(value)) is not None
    }
    roles: dict[str, TimeframeAnalysisResult] = {}
    for role, timeframe in _role_mapping(multi_timeframe).items():
        result = normalized.get(timeframe)
        if result is not None:
            roles[role] = result
    for role in _ROLE_ORDER:
        value = timeframe_results.get(role)
        result = _as_result(value)
        if result is not None:
            roles[role] = result
    return roles


def _current_price(
    roles: Mapping[str, TimeframeAnalysisResult],
    risk: Mapping[str, Any],
) -> float | None:
    for key in ("current_price", "price", "last_price"):
        if risk.get(key) is not None:
            try:
                return float(risk[key])
            except (TypeError, ValueError):
                pass
    for role in ("execution", "tactical", "strategic", "confirmation"):
        result = roles.get(role)
        if result is None:
            continue
        for section in ("support_resistance", "price_action"):
            raw = result.raw_result.get(section)
            if isinstance(raw, Mapping) and raw.get("current_price") is not None:
                try:
                    return float(raw["current_price"])
                except (TypeError, ValueError):
                    pass
    return None


def _candidate_prices(
    action: str,
    roles: Mapping[str, TimeframeAnalysisResult],
    risk: Mapping[str, Any],
) -> tuple[float | None, float | None, float | None, float | None]:
    current = _current_price(roles, risk)
    entry_override = risk.get("entry")
    stop_override = risk.get("stop")
    target_override = risk.get("target")
    for key in ("entry", "stop", "target"):
        if risk.get(key) is not None:
            try:
                value = float(risk[key])
            except (TypeError, ValueError):
                continue
            if key == "entry":
                current = value

    source = (
        roles.get("execution")
        or roles.get("tactical")
        or roles.get("confirmation")
        or roles.get("strategic")
    )
    if source is None:
        return (
            float(entry_override) if entry_override is not None else None,
            float(stop_override) if stop_override is not None else None,
            float(target_override) if target_override is not None else None,
            None,
        )

    supports = sorted(
        (level for level in source.support_levels if level > 0.0),
        reverse=True,
    )
    resistances = sorted(level for level in source.resistance_levels if level > 0.0)
    entry = current
    stop: float | None = None
    target: float | None = None

    raw_price_action = source.raw_result.get("price_action")
    if isinstance(raw_price_action, Mapping):
        decision = raw_price_action.get("decision")
        if isinstance(decision, Mapping):
            raw_action = str(decision.get("action", "")).upper()
            if action in {"BUY", "WAIT_BUY"} and raw_action == "LONG":
                entry = decision.get("entry", entry)
                stop = decision.get("stop")
                target = decision.get("target")
            elif action in {"SELL", "WAIT_SELL", "REDUCE"} and raw_action == "SHORT":
                entry = decision.get("entry", entry)
                stop = decision.get("stop")
                target = decision.get("target")

    if entry is None:
        entry = current
    if action in {"BUY", "WAIT_BUY", "HOLD"}:
        if stop is None:
            below = [level for level in supports if entry is None or level < entry]
            stop = below[0] if below else (min(supports) if supports else None)
        if target is None:
            above = [level for level in resistances if entry is None or level > entry]
            target = above[0] if above else (max(resistances) if resistances else None)
    elif action in {"SELL", "WAIT_SELL", "REDUCE"}:
        if stop is None:
            above = [level for level in resistances if entry is None or level > entry]
            stop = above[0] if above else (max(resistances) if resistances else None)
        if target is None:
            below = [level for level in supports if entry is None or level < entry]
            target = below[0] if below else (min(supports) if supports else None)

    if entry_override is not None:
        try:
            entry = float(entry_override)
        except (TypeError, ValueError):
            pass
    if stop_override is not None:
        try:
            stop = float(stop_override)
        except (TypeError, ValueError):
            pass
    if target_override is not None:
        try:
            target = float(target_override)
        except (TypeError, ValueError):
            pass

    risk_reward: float | None = None
    if entry is not None and stop is not None and target is not None:
        risk_distance = abs(entry - stop)
        reward_distance = abs(target - entry)
        if risk_distance > 0.0:
            risk_reward = round(reward_distance / risk_distance, 4)
    return entry, stop, target, risk_reward


def _confidence(
    multi_timeframe: MultiTimeframeResult,
    coverage_ratio: float,
) -> float:
    directional_score = max(
        multi_timeframe.bullish_score,
        multi_timeframe.bearish_score,
    )
    confidence = (
        multi_timeframe.alignment_score * 0.55
        + directional_score * 0.25
        + multi_timeframe.strategic_score * 0.2
    ) / 100.0
    confidence *= 0.6 + 0.4 * _clamp(coverage_ratio, 0.0, 1.0)
    confidence *= 1.0 - min(multi_timeframe.conflict_score, 100.0) / 200.0
    return round(_clamp(confidence, 0.0, 1.0), 4)


def _conditions(
    action: str,
    multi_timeframe: MultiTimeframeResult,
    roles: Mapping[str, TimeframeAnalysisResult],
) -> tuple[list[str], list[str], list[str], list[str]]:
    reason_codes = [multi_timeframe.summary_state.lower()]
    trigger: list[str] = []
    invalid: list[str] = []
    flags: list[str] = []

    strategic_sign = _direction(multi_timeframe.strategic_trend)
    execution = roles.get("execution")
    execution_sign = _direction(execution.trend if execution else "")

    if multi_timeframe.missing_timeframes:
        flags.append("missing_data")
        reason_codes.append("missing_timeframes")
    if multi_timeframe.conflict_score >= _HIGH_CONFLICT_THRESHOLD:
        flags.append("high_timeframe_conflict")
        reason_codes.append("conflict_high")
    if multi_timeframe.alignment_score < 55.0:
        flags.append("low_alignment")
        reason_codes.append("alignment_low")
    if execution is not None and execution.volatility_score >= 80.0:
        flags.append("high_volatility")
    if execution is None:
        flags.append("execution_data_missing")
        reason_codes.append("execution_data_missing")

    if strategic_sign > 0:
        reason_codes.append("strategic_bullish")
        invalid.append("strategic trend turns bearish")
        invalid.append("price closes below the nearest support")
    elif strategic_sign < 0:
        reason_codes.append("strategic_bearish")
        invalid.append("strategic trend turns bullish")
        invalid.append("price closes above the nearest resistance")
    else:
        flags.append("strategic_trend_unknown")
        reason_codes.append("strategic_trend_unknown")

    if execution_sign > 0:
        reason_codes.append("execution_bullish")
    elif execution_sign < 0:
        reason_codes.append("execution_bearish")
    else:
        flags.append("execution_trigger_absent")

    if action == "BUY":
        trigger.extend(
            [
                "execution close confirms breakout",
                "volume expands during breakout",
                "strategic trend remains bullish",
            ]
        )
        reason_codes.extend(["alignment_high", "buy_trigger_confirmed"])
    elif action == "WAIT_BUY":
        trigger.extend(
            [
                "execution timeframe turns bullish",
                "confirmation timeframe closes above resistance",
                "strategic trend remains bullish",
            ]
        )
        reason_codes.append("wait_for_buy_confirmation")
    elif action == "SELL":
        trigger.extend(
            [
                "execution close confirms breakdown",
                "volume expands during breakdown",
                "strategic trend remains bearish",
            ]
        )
        reason_codes.extend(["alignment_high", "sell_trigger_confirmed"])
    elif action == "WAIT_SELL":
        trigger.extend(
            [
                "execution timeframe turns bearish",
                "confirmation timeframe closes below support",
                "strategic trend remains bearish",
            ]
        )
        reason_codes.append("wait_for_sell_confirmation")
    elif action == "REDUCE":
        trigger.append("price loses the nearest support")
        reason_codes.append("reduce_risk")
    elif action == "HOLD":
        trigger.append("strategic trend remains intact")
        invalid.append("protective stop is breached")
        reason_codes.append("hold_existing_position")
    else:
        trigger.append("wait for clearer multi-timeframe alignment")
        reason_codes.append("wait_for_clear_setup")

    if multi_timeframe.conflict_score >= _HIGH_CONFLICT_THRESHOLD and action == "WATCH":
        reason_codes.append("no_buy_due_conflict")
    return (
        list(dict.fromkeys(trigger)),
        list(dict.fromkeys(invalid)),
        list(dict.fromkeys(flags)),
        list(dict.fromkeys(reason_codes)),
    )


class QuantDecisionEngine:
    """Convert a multi-timeframe profile into a deterministic action."""

    def evaluate(
        self,
        multi_timeframe: MultiTimeframeResult,
        timeframe_results: Mapping[str, Any] | None = None,
        risk: Mapping[str, Any] | None = None,
    ) -> QuantDecision:
        risk_map = dict(risk or {})
        roles = _role_results(timeframe_results, multi_timeframe)
        coverage_ratio = float(multi_timeframe.metadata.get("coverage_ratio", 1.0) or 0.0)
        strategic_sign = _direction(multi_timeframe.strategic_trend)
        execution = roles.get("execution")
        execution_sign = _direction(execution.trend if execution else "")
        tactical = roles.get("tactical")
        tactical_phase = str(tactical.phase if tactical else "").strip().upper()
        confirmation = roles.get("confirmation")
        confirmation_state = (
            str(confirmation.phase if confirmation else "").strip().upper()
        )
        position_state = str(
            risk_map.get("position_state", risk_map.get("position", "flat"))
        ).strip().lower()
        held = position_state in {"held", "holding", "long", "open"}
        high_conflict = (
            multi_timeframe.conflict_score >= _HIGH_CONFLICT_THRESHOLD
            or "HIGH_CONFLICT" in multi_timeframe.summary_state
        )

        action = "WATCH"
        reason_prefix: list[str] = []
        if strategic_sign == 0:
            action = "AVOID" if not multi_timeframe.missing_timeframes else "WATCH"
            reason_prefix.append("strategic_unknown")
        elif high_conflict:
            action = "WATCH"
            reason_prefix.append("high_conflict")
        elif coverage_ratio < 0.5:
            action = "WATCH"
            reason_prefix.append("coverage_low")
        elif strategic_sign > 0:
            if held and multi_timeframe.alignment_score >= 70.0:
                action = "HOLD"
            elif (
                multi_timeframe.alignment_score >= 80.0
                and multi_timeframe.conflict_score < 20.0
                and multi_timeframe.bullish_score >= 60.0
                and execution_sign >= 0
            ):
                action = "BUY"
            elif (
                execution_sign > 0
                and coverage_ratio >= 0.75
                and multi_timeframe.conflict_score < _HIGH_CONFLICT_THRESHOLD
            ):
                action = "WAIT_BUY"
            elif (
                tactical_phase == "PULLBACK"
                and confirmation_state in {"CONSOLIDATION", "BREAKOUT"}
                and coverage_ratio >= 0.75
            ):
                action = "WAIT_BUY"
            else:
                action = "WATCH"
        elif strategic_sign < 0:
            if held and multi_timeframe.alignment_score >= 80.0:
                action = "SELL"
            elif multi_timeframe.alignment_score >= 70.0 and held:
                action = "REDUCE"
            elif (
                multi_timeframe.alignment_score >= 80.0
                and multi_timeframe.conflict_score < 20.0
                and multi_timeframe.bearish_score >= 60.0
                and execution_sign <= 0
            ):
                action = "SELL"
            elif (
                execution_sign < 0
                and coverage_ratio >= 0.75
                and multi_timeframe.conflict_score < _HIGH_CONFLICT_THRESHOLD
            ):
                action = "WAIT_SELL"
            else:
                action = "WATCH"

        trigger, invalid, flags, reason_codes = _conditions(action, multi_timeframe, roles)
        reason_codes = list(dict.fromkeys(reason_prefix + reason_codes))
        entry, stop, target, risk_reward = _candidate_prices(action, roles, risk_map)
        confidence = _confidence(multi_timeframe, coverage_ratio)
        action_caps = {
            "AVOID": 0.55,
            "WATCH": 0.6,
            "WAIT_BUY": 0.78,
            "WAIT_SELL": 0.78,
            "HOLD": 0.8,
            "REDUCE": 0.78,
        }
        confidence = min(confidence, action_caps.get(action, 0.9))
        if high_conflict:
            confidence = min(confidence, 0.55)

        return QuantDecision(
            action=action,
            confidence=round(confidence, 4),
            entry=entry if action in {"BUY", "WAIT_BUY", "HOLD"} else None,
            stop=stop if action in {"BUY", "WAIT_BUY", "HOLD", "REDUCE"} else None,
            target=target if action in {"BUY", "WAIT_BUY", "HOLD", "SELL"} else None,
            risk_reward=risk_reward if action in {"BUY", "WAIT_BUY", "SELL"} else None,
            trigger_conditions=trigger,
            invalid_conditions=invalid,
            risk_flags=flags,
            reason_codes=reason_codes,
        )


__all__ = ["QuantDecisionEngine"]

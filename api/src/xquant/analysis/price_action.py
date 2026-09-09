"""Deterministic local price-action analysis.

The engine deliberately uses only the caller-supplied closed bars.  It is a
research/simulation feature and never produces orders.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

_MIN_BARS = 60
_DIRECTIONS = {"bullish", "bearish", "neutral"}


@dataclass(frozen=True)
class _Bar:
    seq: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _number(value: Any, field: str, index: int) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"第 {index + 1} 根K线的 {field} 必须是数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"第 {index + 1} 根K线的 {field} 必须是数字") from exc
    if not math.isfinite(result):
        raise ValueError(f"第 {index + 1} 根K线的 {field} 必须是有限数字")
    return result


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    result = round(float(value), digits)
    return 0.0 if result == 0 else result


def _parse_bars(raw_bars: Sequence[Mapping[str, Any]]) -> list[_Bar]:
    bars: list[_Bar] = []
    previous_session: str | None = None
    for index, raw in enumerate(raw_bars):
        if not isinstance(raw, Mapping):
            raise TypeError(f"第 {index + 1} 根K线必须是 Mapping")
        session = raw.get("session_id", raw.get("session", raw.get("time", raw.get("date"))))
        session_id = "" if session is None else str(session)
        if previous_session is not None and session_id and session_id <= previous_session:
            raise ValueError(f"K线 session 顺序必须旧到新且不重复：{session_id}")
        previous_session = session_id or previous_session

        open_price = _number(raw.get("open"), "open", index)
        high = _number(raw.get("high"), "high", index)
        low = _number(raw.get("low"), "low", index)
        close = _number(raw.get("close"), "close", index)
        volume = _number(raw.get("volume", 0.0), "volume", index)
        if high < max(open_price, close) - 1e-12 or low > min(open_price, close) + 1e-12:
            raise ValueError(f"第 {index + 1} 根K线的 OHLC 关系无效")
        if high < low:
            raise ValueError(f"第 {index + 1} 根K线的 high 不能小于 low")
        if volume < 0:
            raise ValueError(f"第 {index + 1} 根K线的 volume 不能为负")
        bars.append(_Bar(index + 1, open_price, high, low, close, volume))
    return bars


def _ema(values: Sequence[float], period: int = 20) -> list[float]:
    result = [math.nan] * len(values)
    if len(values) < period:
        return result
    previous = sum(values[:period]) / period
    result[period - 1] = previous
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        previous = alpha * values[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _atr(bars: Sequence[_Bar], period: int = 14) -> list[float]:
    result = [math.nan] * len(bars)
    if not bars:
        return result
    true_ranges = [bars[0].high - bars[0].low]
    for index in range(1, len(bars)):
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                bars[index].high - bars[index].low,
                abs(bars[index].high - previous_close),
                abs(bars[index].low - previous_close),
            )
        )
    if len(true_ranges) < period:
        return result
    previous = sum(true_ranges[:period]) / period
    result[period - 1] = previous
    for index in range(period, len(bars)):
        previous = ((period - 1) * previous + true_ranges[index]) / period
        result[index] = previous
    return result


def _swing_pivots(bars: Sequence[_Bar]) -> list[dict[str, Any]]:
    pivots: list[dict[str, Any]] = []
    for index in range(2, len(bars) - 2):
        window = bars[index - 2 : index + 3]
        center = bars[index]
        if center.high > max(bar.high for bar in window if bar.seq != center.seq):
            pivots.append({"seq": center.seq, "kind": "high", "price": _round(center.high)})
        if center.low < min(bar.low for bar in window if bar.seq != center.seq):
            pivots.append({"seq": center.seq, "kind": "low", "price": _round(center.low)})
    return pivots


def _label_swing_structure(pivots: Sequence[Mapping[str, Any]]) -> str:
    highs = [item["price"] for item in pivots if item["kind"] == "high"]
    lows = [item["price"] for item in pivots if item["kind"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return "HH+HL"
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return "LL+LH"
    return "mixed"


def _overlap_mean(bars: Sequence[_Bar], window: int = 10) -> float | None:
    if len(bars) < 2:
        return None
    sample = bars[-min(window, len(bars)) :]
    ratios: list[float] = []
    for previous, current in pairwise(sample):
        denominator = max(previous.high, current.high) - min(previous.low, current.low)
        overlap = max(0.0, min(previous.high, current.high) - max(previous.low, current.low))
        if denominator > 0:
            ratios.append(overlap / denominator)
    return _round(sum(ratios) / len(ratios), 3) if ratios else None


def _trend_bars(bars: Sequence[_Bar], atr_value: float) -> tuple[int, int]:
    bull = bear = 0
    threshold = max(atr_value * 0.45, 1e-12)
    for bar in bars:
        body = abs(bar.close - bar.open)
        bar_range = max(bar.high - bar.low, 1e-12)
        if body < threshold and body / bar_range < 0.45:
            continue
        if bar.close > bar.open:
            bull += 1
        elif bar.close < bar.open:
            bear += 1
    return bull, bear


def _direction_vote(
    bars: Sequence[_Bar],
    ema_values: Sequence[float],
    atr_value: float,
    *,
    start: int,
) -> tuple[str, int]:
    slice_bars = list(bars[start:])
    if len(slice_bars) < 5:
        return "neutral", 0
    score = 0
    slope_span = min(5, len(slice_bars) - 1)
    slope = ema_values[-1] - ema_values[-1 - slope_span]
    slope_threshold = atr_value * 0.06
    if slope > slope_threshold:
        score += 1
    elif slope < -slope_threshold:
        score -= 1

    half = len(slice_bars) // 2
    near = sum(bar.close for bar in slice_bars[half:]) / (len(slice_bars) - half)
    far = sum(bar.close for bar in slice_bars[:half]) / half
    if near - far > atr_value * 0.12:
        score += 1
    elif near - far < -atr_value * 0.12:
        score -= 1

    pivots = _swing_pivots(slice_bars)
    highs = [item["price"] for item in pivots if item["kind"] == "high"]
    lows = [item["price"] for item in pivots if item["kind"] == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            score += 1
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            score -= 1

    bull, bear = _trend_bars(slice_bars, atr_value)
    if bull >= bear * 2 and bull >= 3:
        score += 1
    elif bear >= bull * 2 and bear >= 3:
        score -= 1

    overlap = _overlap_mean(slice_bars, window=len(slice_bars))
    if overlap is not None and overlap < 0.55:
        if score > 0:
            score += 1
        elif score < 0:
            score -= 1
    if score >= 2:
        return "bullish", score
    if score <= -2:
        return "bearish", score
    return "neutral", score


def _detect_recent_spike(bars: Sequence[_Bar], atr_value: float) -> str | None:
    sample = bars[-min(8, len(bars)) :]
    if len(sample) < 4:
        return None
    bull, bear = _trend_bars(sample, atr_value)
    overlap = _overlap_mean(sample, window=len(sample))
    if overlap is not None and overlap > 0.68:
        return None
    if bull >= 3 and bull >= bear * 2:
        return "bullish"
    if bear >= 3 and bear >= bull * 2:
        return "bearish"
    return None


def _breakout_events(bars: Sequence[_Bar], atr_value: float) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    tolerance = max(atr_value * 0.01, 1e-10)
    up_trigger: int | None = None
    up_level: float | None = None
    down_trigger: int | None = None
    down_level: float | None = None

    for index, bar in enumerate(bars):
        if index == 0:
            continue
        prior = bars[:index]
        prior_high = max(item.high for item in prior)
        prior_low = min(item.low for item in prior)

        if bar.close > prior_high + tolerance:
            up_trigger = index
            up_level = prior_high
            events.append(
                {
                    "direction": "up",
                    "event": "breakout",
                    "level": _round(prior_high),
                    "trigger_seq": bar.seq,
                }
            )
        elif up_trigger is not None and up_level is not None:
            if bar.close < up_level - tolerance:
                events.append(
                    {
                        "direction": "up",
                        "event": "failed",
                        "level": _round(up_level),
                        "trigger_seq": bar.seq,
                    }
                )
                up_trigger = None
                up_level = None
            elif bar.low <= up_level + atr_value * 0.25 and bar.close > up_level:
                events.append(
                    {
                        "direction": "up",
                        "event": "test",
                        "level": _round(up_level),
                        "trigger_seq": bar.seq,
                    }
                )

        if bar.close < prior_low - tolerance:
            down_trigger = index
            down_level = prior_low
            events.append(
                {
                    "direction": "down",
                    "event": "breakout",
                    "level": _round(prior_low),
                    "trigger_seq": bar.seq,
                }
            )
        elif down_trigger is not None and down_level is not None:
            if bar.close > down_level + tolerance:
                events.append(
                    {
                        "direction": "down",
                        "event": "failed",
                        "level": _round(down_level),
                        "trigger_seq": bar.seq,
                    }
                )
                down_trigger = None
                down_level = None
            elif bar.high >= down_level - atr_value * 0.25 and bar.close < down_level:
                events.append(
                    {
                        "direction": "down",
                        "event": "test",
                        "level": _round(down_level),
                        "trigger_seq": bar.seq,
                    }
                )

    last_bar = bars[-1]
    quality = "none"
    if up_trigger is not None and up_level is not None and last_bar.close > up_level:
        quality = "surviving" if up_trigger < len(bars) - 1 else "close_breakout"
    elif down_trigger is not None and down_level is not None and last_bar.close < down_level:
        quality = "surviving" if down_trigger < len(bars) - 1 else "close_breakout"
    else:
        later_by_kind: dict[tuple[str, str], int] = {}
        for index, event in enumerate(events):
            later_by_kind[(event["direction"], event["event"])] = index
        prior_high = max(item.high for item in bars[:-1]) if len(bars) > 1 else last_bar.high
        prior_low = min(item.low for item in bars[:-1]) if len(bars) > 1 else last_bar.low
        if (
            last_bar.high > prior_high
            and last_bar.close < prior_high
            or last_bar.low < prior_low
            and last_bar.close > prior_low
        ):
            quality = "wick_probe"
        recent_boundary = max(0, len(bars) - 8)
        if quality == "none":
            for direction in ("up", "down"):
                failed_index = later_by_kind.get((direction, "failed"))
                test_index = later_by_kind.get((direction, "test"))
                if failed_index is not None and failed_index >= recent_boundary:
                    quality = "failed"
                    break
                if test_index is not None and test_index >= recent_boundary:
                    quality = "testing"
                    break
    return events[-8:], quality


def _hl_count(bars: Sequence[_Bar]) -> dict[str, Any]:
    sample = bars[-min(20, len(bars)) :]
    bull = bear = 0
    for previous, current in pairwise(sample):
        if current.high > previous.high:
            bull += 1
        if current.low < previous.low:
            bear += 1
    return {
        "bull_count": bull,
        "bear_count": bear,
        "bull_candidate": "none" if bull == 0 else f"h{min(bull, 3)}",
        "bear_candidate": "none" if bear == 0 else f"l{min(bear, 3)}",
    }


def _structure_levels(
    bars: Sequence[_Bar], pivots: Sequence[Mapping[str, Any]], close: float
) -> tuple[list[float], list[float]]:
    supports: list[float] = []
    resistances: list[float] = []
    seen: set[float] = set()
    for pivot in reversed(pivots):
        price = float(pivot["price"])
        key = round(price, 8)
        if key in seen:
            continue
        seen.add(key)
        if pivot["kind"] == "low" and price < close:
            supports.append(price)
        elif pivot["kind"] == "high" and price > close:
            resistances.append(price)

    if not supports:
        recent_lows = [bar.low for bar in bars[-12:]]
        supports = [min(recent_lows)] if min(recent_lows) < close else []
    if not resistances:
        recent_highs = [bar.high for bar in bars[-12:]]
        resistances = [max(recent_highs)] if max(recent_highs) > close else []
    return sorted(supports, reverse=True)[:3], sorted(resistances)[:3]


def _direction_label(score: int) -> str:
    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def _resolve_stance(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return (
        normalized if normalized in {"conservative", "balanced", "aggressive"} else "conservative"
    )


def _proposal(
    action: str,
    bars: Sequence[_Bar],
    atr_value: float,
    supports: Sequence[float],
    resistances: Sequence[float],
    range_high: float,
    range_low: float,
    breakout_level: float | None,
) -> tuple[float, float, float] | None:
    entry = bars[-1].close
    if action == "LONG":
        structural_stop_candidates = [value for value in supports if value < entry]
        if breakout_level is not None and breakout_level < entry:
            structural_stop_candidates.append(breakout_level)
        swing_low = min(bar.low for bar in bars[-8:])
        if structural_stop_candidates:
            stop = max(structural_stop_candidates) - atr_value * 0.35
        else:
            stop = swing_low - atr_value * 0.35
        stop = min(stop, entry - atr_value * 0.5)
        target_candidates = [value for value in resistances if value > entry + atr_value * 0.2]
        if target_candidates:
            target = max(target_candidates)
        else:
            target = max(range_high, max(bar.high for bar in bars[-8:])) + atr_value * 0.25
        target = max(target, entry + atr_value)
        return entry, stop, target

    if action == "SHORT":
        structural_stop_candidates = [value for value in resistances if value > entry]
        if breakout_level is not None and breakout_level > entry:
            structural_stop_candidates.append(breakout_level)
        swing_high = max(bar.high for bar in bars[-8:])
        if structural_stop_candidates:
            stop = min(structural_stop_candidates) + atr_value * 0.35
        else:
            stop = swing_high + atr_value * 0.35
        stop = max(stop, entry + atr_value * 0.5)
        target_candidates = [value for value in supports if value < entry - atr_value * 0.2]
        if target_candidates:
            target = min(target_candidates)
        else:
            target = min(range_low, min(bar.low for bar in bars[-8:])) - atr_value * 0.25
        target = min(target, entry - atr_value)
        return entry, stop, target
    return None


def analyze_price_action(
    bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str = "",
    timeframe: str = "1d",
    lookback: int = 120,
    risk_fraction: float = 0.01,
    min_rr: float = 1.5,
    stance: str = "conservative",
) -> dict[str, Any]:
    """Analyze closed OHLCV bars with a deterministic local price-action engine."""
    if lookback < _MIN_BARS:
        raise ValueError("数据不足：至少需要 60 根K线")
    parsed = _parse_bars(bars)
    if len(parsed) < _MIN_BARS:
        raise ValueError("数据不足：至少需要 60 根K线")

    selected = parsed[-min(lookback, len(parsed)) :]
    closes = [bar.close for bar in selected]
    ema_values = _ema(closes, 20)
    atr_values = _atr(selected, 14)
    atr_value = atr_values[-1]
    if not math.isfinite(atr_value) or atr_value <= 0:
        atr_value = max(selected[-1].high - selected[-1].low, 1e-10)

    current_price = selected[-1].close
    range_high = max(bar.high for bar in selected)
    range_low = min(bar.low for bar in selected)
    range_width = range_high - range_low
    price_position = (current_price - range_low) / range_width if range_width > 0 else 0.5
    overlap_mean_10 = _overlap_mean(selected, 10)
    pivots = _swing_pivots(selected)
    swing_structure = _label_swing_structure(pivots)
    supports, resistances = _structure_levels(selected, pivots, current_price)
    breakout_events, breakout_quality = _breakout_events(selected, atr_value)
    hl_count = _hl_count(selected)

    recent_direction, recent_score = _direction_vote(
        selected[-10:], ema_values[-10:], atr_value, start=0
    )
    trading_direction, trading_score = _direction_vote(
        selected[-20:], ema_values[-20:], atr_value, start=0
    )
    background_start = max(0, len(selected) - 40)
    background_direction, background_score = _direction_vote(
        selected[background_start : max(background_start + 5, len(selected) - 10)],
        ema_values[background_start : max(background_start + 5, len(selected) - 10)],
        atr_value,
        start=0,
    )
    direction = _direction_label(trading_score + max(-1, min(1, recent_score)))
    background_direction = _direction_label(background_score)
    recent_spike = _detect_recent_spike(selected, atr_value)
    scale_conflict = bool(
        (recent_direction == "bullish" and trading_direction == "bearish")
        or (recent_direction == "bearish" and trading_direction == "bullish")
        or (background_direction == "bullish" and direction == "bearish")
        or (background_direction == "bearish" and direction == "bullish")
    )

    patterns: list[str] = []
    if swing_structure == "HH+HL":
        patterns.append("higher_highs_higher_lows")
    elif swing_structure == "LL+LH":
        patterns.append("lower_lows_lower_highs")
    if breakout_quality != "none":
        patterns.append(f"breakout_{breakout_quality}")
    if overlap_mean_10 is not None and overlap_mean_10 >= 0.72:
        patterns.append("overlap")
    if hl_count["bull_count"] >= 3:
        patterns.append("h3")
    if hl_count["bear_count"] >= 3:
        patterns.append("l3")
    if price_position <= 0.3:
        patterns.append("range_boundary_low")
    elif price_position >= 0.7:
        patterns.append("range_boundary_high")
    elif 0.35 <= price_position <= 0.65:
        patterns.append("middle_range")

    nearest_support = supports[0] if supports else None
    nearest_resistance = resistances[0] if resistances else None
    near_support = bool(
        nearest_support is not None
        and current_price >= nearest_support - atr_value * 0.1
        and current_price - nearest_support <= atr_value * 0.9
    )
    near_resistance = bool(
        nearest_resistance is not None
        and current_price <= nearest_resistance + atr_value * 0.1
        and nearest_resistance - current_price <= atr_value * 0.9
    )
    if near_support:
        patterns.append("near_support")
    if near_resistance:
        patterns.append("near_resistance")

    if breakout_quality == "surviving":
        last_event = next(
            (event for event in reversed(breakout_events) if event["event"] == "breakout"),
            None,
        )
        breakout_direction = last_event["direction"] if last_event else None
        if breakout_direction == "up":
            cycle_position = "breakout"
        elif breakout_direction == "down":
            cycle_position = "breakdown"
        else:
            cycle_position = "breakout"
    elif breakout_quality in {"close_breakout", "wick_probe", "testing", "failed"}:
        cycle_position = "breakout_attempt"
    elif direction == "bullish" and near_support:
        cycle_position = "trend_pullback"
    elif direction == "bearish" and near_resistance:
        cycle_position = "trend_rebound"
    elif direction in {"bullish", "bearish"}:
        cycle_position = "trending"
    elif 0.35 <= price_position <= 0.65:
        cycle_position = "trading_range"
    else:
        cycle_position = "range_boundary"

    reason_codes: list[str] = []
    candidate: tuple[float, float, float] | None = None
    breakout_level_for_stop: float | None = None
    if breakout_quality == "surviving":
        last_event = next(
            (event for event in reversed(breakout_events) if event["event"] == "breakout"),
            None,
        )
        if last_event:
            breakout_level_for_stop = float(last_event["level"])

    bullish_context = (
        (direction == "bullish" or swing_structure == "HH+HL")
        and recent_direction != "bearish"
        and not scale_conflict
    )
    bearish_context = (
        (direction == "bearish" or swing_structure == "LL+LH")
        and recent_direction != "bullish"
        and not scale_conflict
    )
    surviving_up = breakout_quality == "surviving" and cycle_position == "breakout"
    surviving_down = breakout_quality == "surviving" and cycle_position == "breakdown"
    overlap_blocked = overlap_mean_10 is not None and overlap_mean_10 >= 0.78
    middle_range = 0.35 <= price_position <= 0.65 and breakout_quality not in {
        "surviving",
        "close_breakout",
    }

    if overlap_blocked:
        action = "WAIT"
        reason_codes.extend(["overlap_too_high", "barbwire"])
    elif scale_conflict:
        action = "WAIT"
        reason_codes.append("direction_conflict")
    elif (
        middle_range
        and not (bullish_context and near_support)
        and not (bearish_context and near_resistance)
    ):
        action = "WAIT"
        reason_codes.append("middle_of_range")
    elif (bullish_context and near_support) or surviving_up:
        action = "LONG"
        reason_codes.extend(
            ["bullish_structure", "near_support" if near_support else "surviving_breakout"]
        )
    elif (bearish_context and near_resistance) or surviving_down:
        action = "SHORT"
        reason_codes.extend(
            ["bearish_structure", "near_resistance" if near_resistance else "surviving_breakdown"]
        )
    else:
        action = "WAIT"
        reason_codes.append("no_consistent_setup")

    if action in {"LONG", "SHORT"}:
        candidate = _proposal(
            action,
            selected,
            atr_value,
            supports,
            resistances,
            range_high,
            range_low,
            breakout_level_for_stop,
        )

    entry = stop = target = rr = None
    invalidation: str | None = None
    confidence = 0
    if candidate is not None:
        entry, stop, target = candidate
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0 else 0.0
        if rr < min_rr:
            action = "WAIT"
            reason_codes.append("rr_below_minimum")
        else:
            structure_score = 18 if swing_structure in {"HH+HL", "LL+LH"} else 0
            direction_score = (
                16 if direction == ("bullish" if action == "LONG" else "bearish") else 0
            )
            location_score = (
                15
                if action == "LONG" and near_support
                else 15
                if action == "SHORT" and near_resistance
                else 12
            )
            background_score_bonus = (
                10
                if background_direction == ("bullish" if action == "LONG" else "bearish")
                else 4
                if background_direction == "neutral"
                else 0
            )
            breakout_score = 14 if breakout_quality == "surviving" else 0
            hl_score = (
                5
                if (action == "LONG" and hl_count["bull_count"] >= hl_count["bear_count"])
                or (action == "SHORT" and hl_count["bear_count"] >= hl_count["bull_count"])
                else 0
            )
            confidence = (
                structure_score
                + direction_score
                + location_score
                + background_score_bonus
                + breakout_score
                + hl_score
            )
            if overlap_mean_10 is not None and overlap_mean_10 >= 0.72:
                confidence -= 8
            if recent_spike == ("bullish" if action == "LONG" else "bearish"):
                confidence += 4
            confidence = max(0, min(confidence, 70 if stance == "conservative" else 90))

    if action == "WAIT":
        confidence = min(confidence, 35)
        if "rr_below_minimum" in reason_codes:
            invalidation = "保持等待：结构位方案的风险回报未达到 min_rr。"
        elif "direction_conflict" in reason_codes:
            invalidation = "等待大小周期方向重新一致。"
        elif "middle_of_range" in reason_codes:
            invalidation = "等待价格回到区间边界或形成已收盘突破。"
        elif nearest_support is not None and nearest_resistance is not None:
            invalidation = (
                f"等待收盘突破 {nearest_resistance} 或跌破 {nearest_support} 后重新评估。"
            )
        else:
            invalidation = "等待明确的价格结构形成。"
        if "rr_below_minimum" not in reason_codes:
            entry = stop = target = rr = None
    else:
        if action == "LONG":
            invalidation = f"收盘跌破 {stop} 或最近支撑失效。"
        else:
            invalidation = f"收盘升破 {stop} 或最近阻力失效。"

    if action == "LONG":
        reasoning = "多头结构且价格接近支撑，使用摆动/区间与 ATR 缓冲定义防守，再按结构位评估目标。"
    elif action == "SHORT":
        reasoning = "空头结构且价格接近阻力，使用摆动/区间与 ATR 缓冲定义防守，再按结构位评估目标。"
    elif "rr_below_minimum" in reason_codes:
        reasoning = "方向与位置符合条件，但结构化三价的风险回报不足，保守处理为等待。"
    else:
        reasoning = "方向、结构位置或突破质量不足，保守等待更清晰的已收盘确认。"

    stance_name = _resolve_stance(stance)
    trend_detail = {
        "recent": recent_direction,
        "recent_score": recent_score,
        "trading": trading_direction,
        "trading_score": trading_score,
        "background": background_direction,
        "background_score": background_score,
    }
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_used": len(selected),
        "current_price": _round(current_price),
        "ema20": _round(ema_values[-1]),
        "atr": _round(atr_value),
        "atr_pct": _round(atr_value / current_price * 100 if current_price else None, 4),
        "simulation_only": True,
        "market_context": {
            "direction": direction,
            "cycle_position": cycle_position,
            "price_position": _round(price_position, 3),
            "range_high": _round(range_high),
            "range_low": _round(range_low),
            "overlap_mean_10": overlap_mean_10,
            "trend_detail": trend_detail,
            "background_direction": background_direction,
            "recent_spike": recent_spike,
            "scale_conflict": scale_conflict,
        },
        "features": {
            "swing_structure": swing_structure,
            "swings": pivots[-12:],
            "breakout_quality": breakout_quality,
            "breakout_events": breakout_events,
            "patterns": patterns,
            "supports": [_round(value) for value in supports],
            "resistances": [_round(value) for value in resistances],
            "hl_count": hl_count,
        },
        "decision": {
            "action": action,
            "confidence": confidence,
            "entry": _round(entry),
            "stop": _round(stop),
            "target": _round(target),
            "rr": _round(rr, 3),
            "risk_fraction": risk_fraction,
            "reason_codes": reason_codes,
            "reasoning": reasoning,
            "invalidation": invalidation,
        },
        "meta": {
            "engine": "xq_pa_local_v1",
            "stance": stance_name,
            "min_rr": min_rr,
        },
    }

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

_MIN_BARS = 60
_MIN_DISTANCE_ATR = 0.5
_MAX_DISTANCE_ATR = 5.0
_MIN_ZONE_WIDTH_ATR = 0.3
_MAX_ZONE_WIDTH_ATR = 1.2
_VOLUME_BIN_ATR = 0.25
_VOLUME_HALF_LIFE = 60.0
_MAX_PROFILE_BINS = 240
_TOUCH_GAP_BARS = 5
_TOUCH_HOLD_BARS = 10
_TOUCH_BREAK_ATR = 0.5
_TOUCH_TARGET_ATR = 1.0
_STALE_HALF_LIFE = 120.0
_DIRECTION_LABELS = {"long": "只做多", "short": "只做空", "both": "多空都做"}

_CAVEAT = (
    "支撑阻力是规则化统计区域，不预测未来价格；"
    "结果仅用于研究和模拟，不构成投资建议。"
)


class _PriceProfile(NamedTuple):
    price_min: float
    bin_width: float
    volume_density: tuple[float, ...]
    close_density: tuple[float, ...]
    vp_strength: tuple[float, ...]
    close_strength: tuple[float, ...]


def detect_support_resistance(
    bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str = "",
    timeframe: str = "1d",
    lookback: int = 250,
    n_zones: int = 6,
    direction: str = "both",
) -> dict[str, Any]:
    """Detect deterministic support and resistance zones from OHLCV bars.

    The engine combines volume-at-price density with confirmed two-sided swing
    pivots. All distances are normalized by ATR(14), so the same rules work
    for A-share and generic instruments.
    """
    normalized = _normalize_bars(bars)
    if len(normalized) < _MIN_BARS:
        raise ValueError("数据不足：至少需要 60 根K线")

    selected = normalized[-_bounded_lookback(lookback) :]
    highs = [item["high"] for item in selected]
    lows = [item["low"] for item in selected]
    closes = [item["close"] for item in selected]
    volumes = [item["volume"] for item in selected]
    bars_used = len(selected)
    current_price = closes[-1]
    atr_values = _atr_series(highs, lows, closes)
    atr = atr_values[-1] or max(abs(current_price) * 1e-3, 1e-9)
    profile = _build_price_profile(highs, lows, closes, volumes, atr)

    candidates = _swing_pivot_candidates(highs, lows)
    candidates.extend(_volume_density_candidates(highs, lows, volumes, atr, profile=profile))
    zones = _build_zones(
        candidates,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        current_price=current_price,
        atr=atr,
        atr_values=atr_values,
        bars_used=bars_used,
        profile=profile,
    )
    zones = _mark_higher_timeframe_resonance(zones, selected, timeframe, atr)
    zones = _filter_and_limit(zones, current_price, atr, n_zones, direction)

    trend = _trend(closes)
    summary = _build_summary(zones, trend, current_price, atr, direction)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_used": bars_used,
        "current_price": _round(current_price),
        "atr": _round(atr),
        "atr_pct": _round(atr / current_price * 100.0 if current_price else 0.0),
        "trend": trend,
        "levels": zones,
        "summary": summary,
        "meta": {
            "engine": "xq_sr_fusion_v3",
            "simulation_only": True,
            "lookback": _bounded_lookback(lookback),
            "n_zones": _bounded_zone_count(n_zones),
            "direction": _normalized_direction(direction),
            "calibrated": False,
            "probability_model": None,
        },
    }


def _normalize_bars(bars: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    normalized: list[dict[str, float]] = []
    for index, bar in enumerate(bars):
        if not isinstance(bar, Mapping):
            raise TypeError(f"第 {index + 1} 根K线不是映射对象")
        try:
            open_price = _as_float(_field(bar, ("open", "o")), "open")
            high = _as_float(_field(bar, ("high", "h")), "high")
            low = _as_float(_field(bar, ("low", "l")), "low")
            close = _as_float(_field(bar, ("close", "c")), "close")
            volume = _as_float(_field(bar, ("volume", "vol", "v")), "volume")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {index + 1} 根K线数值无效：{exc}") from exc

        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise ValueError(f"第 {index + 1} 根K线的 OHLC 关系无效")
        normalized.append(
            {
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(volume, 0.0),
            }
        )
    return normalized


def _field(bar: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    lowered: dict[str, Any] = {}
    for key, value in bar.items():
        if isinstance(key, str):
            lowered[key.strip().lower()] = value
    for name in names:
        if name in lowered and lowered[name] is not None:
            return lowered[name]
    raise KeyError("缺少字段 " + "/".join(names))


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是数字")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        result = float(value.strip().replace(",", ""))
    else:
        raise TypeError(f"{field_name} 必须是数字或数字字符串")
    if not math.isfinite(result):
        raise ValueError(f"{field_name} 必须是有限数字")
    return result


def _bounded_lookback(lookback: int) -> int:
    try:
        value = int(lookback)
    except (TypeError, ValueError):
        return _MIN_BARS
    return max(_MIN_BARS, value)


def _bounded_zone_count(n_zones: int) -> int:
    try:
        value = int(n_zones)
    except (TypeError, ValueError):
        return 6
    return min(12, max(1, value))


def _normalized_direction(direction: str) -> str:
    value = str(direction).strip().lower()
    return value if value in {"long", "short", "both"} else "both"


def _atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> list[float | None]:
    if len(closes) < 14:
        return [None] * len(closes)
    true_ranges = [highs[0] - lows[0]]
    for index in range(1, len(closes)):
        previous_close = closes[index - 1]
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - previous_close),
                abs(lows[index] - previous_close),
            )
        )
    atr_values: list[float | None] = [None] * len(closes)
    atr = sum(true_ranges[:14]) / 14.0
    atr_values[13] = atr if math.isfinite(atr) and atr > 0.0 else None
    for index in range(14, len(true_ranges)):
        atr = (atr * 13.0 + true_ranges[index]) / 14.0
        atr_values[index] = atr if math.isfinite(atr) and atr > 0.0 else None
    return atr_values


def _atr14(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> float | None:
    values = _atr_series(highs, lows, closes)
    return values[-1] if values else None


def _swing_pivot_candidates(highs: Sequence[float], lows: Sequence[float]) -> list[float]:
    candidates: list[float] = []
    for index in range(2, len(highs) - 2):
        high = highs[index]
        if (
            high > highs[index - 1]
            and high > highs[index - 2]
            and high > highs[index + 1]
            and high > highs[index + 2]
        ):
            candidates.append(high)
        low = lows[index]
        if (
            low < lows[index - 1]
            and low < lows[index - 2]
            and low < lows[index + 1]
            and low < lows[index + 2]
        ):
            candidates.append(low)
    return candidates


def _volume_density_candidates(
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    atr: float,
    *,
    profile: _PriceProfile | None = None,
) -> list[float]:
    profile = profile or _build_price_profile(highs, lows, (), volumes, atr)
    strengths = profile.vp_strength
    if not strengths:
        return []

    peaks = _profile_peaks(strengths)
    if not peaks:
        peaks = [max(range(len(strengths)), key=strengths.__getitem__)]

    selected: list[int] = []
    minimum_separation = max(1, round(0.75 * atr / profile.bin_width))
    for index in sorted(peaks, key=strengths.__getitem__, reverse=True):
        if all(abs(index - chosen) >= minimum_separation for chosen in selected):
            selected.append(index)
        if len(selected) == 10:
            break
    return sorted(
        _profile_price(profile, index)
        for index in selected
        if strengths[index] > 0.0
    )


def _build_price_profile(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    atr: float,
) -> _PriceProfile:
    if not highs:
        return _PriceProfile(0.0, 1.0, (), (), (), ())

    count = len(highs)
    decayed_volumes = [
        max(float(volume), 0.0) * 2.0 ** (-(count - 1 - index) / _VOLUME_HALF_LIFE)
        for index, volume in enumerate(volumes)
    ]
    price_max = max(highs)
    price_min = min(lows)
    core_span = max(price_max - price_min, atr * _VOLUME_BIN_ATR, 1e-12)
    bin_width = max(atr * _VOLUME_BIN_ATR, core_span / _MAX_PROFILE_BINS)
    price_min -= bin_width
    span = max(price_max - price_min, bin_width)
    n_bins = max(1, math.ceil(span / bin_width) + 1)
    if n_bins > _MAX_PROFILE_BINS:
        bin_width = span / max(_MAX_PROFILE_BINS - 1, 1)
        n_bins = _MAX_PROFILE_BINS

    volume_density = [0.0] * n_bins
    close_density = [0.0] * n_bins
    for index, (high, low, volume) in enumerate(zip(highs, lows, decayed_volumes, strict=True)):
        first = _profile_index(price_min, bin_width, n_bins, low)
        last = _profile_index(price_min, bin_width, n_bins, high)
        if last < first:
            first, last = last, first

        bar_width = max(high - low, bin_width * 1e-12)
        allocations: list[tuple[int, float]] = []
        allocation_total = 0.0
        for bin_index in range(first, last + 1):
            bin_low = price_min + bin_index * bin_width
            bin_high = bin_low + bin_width
            overlap = min(high, bin_high) - max(low, bin_low)
            if overlap <= 0.0:
                continue
            allocation = overlap / bar_width
            allocations.append((bin_index, allocation))
            allocation_total += allocation
        if allocation_total <= 0.0:
            allocations = [(first, 1.0)]
            allocation_total = 1.0
        for bin_index, allocation in allocations:
            volume_density[bin_index] += volume * allocation / allocation_total

        if index < len(closes):
            close_index = _profile_index(price_min, bin_width, n_bins, closes[index])
            close_density[close_index] += volume

    volume_density = _smooth_density(volume_density)
    close_density = _smooth_density(close_density)
    return _PriceProfile(
        price_min=price_min,
        bin_width=bin_width,
        volume_density=tuple(volume_density),
        close_density=tuple(close_density),
        vp_strength=_normalized_profile_strength(volume_density),
        close_strength=_normalized_profile_strength(close_density),
    )


def _profile_index(price_min: float, bin_width: float, n_bins: int, price: float) -> int:
    return min(n_bins - 1, max(0, math.floor((price - price_min) / bin_width)))


def _profile_price(profile: _PriceProfile, index: int) -> float:
    return profile.price_min + (index + 0.5) * profile.bin_width


def _smooth_density(values: Sequence[float]) -> list[float]:
    if len(values) < 3:
        return list(values)
    weighted_values: list[float] = []
    for index in range(len(values)):
        weighted = 0.0
        for offset, weight in ((-1, 0.25), (0, 0.5), (1, 0.25)):
            neighbor = index + offset
            if 0 <= neighbor < len(values):
                weighted += values[neighbor] * weight
        weighted_values.append(weighted)
    weighted_total = sum(weighted_values)
    if weighted_total <= 1e-12:
        return [0.0 for _ in values]
    scale = sum(values) / weighted_total
    return [value * scale for value in weighted_values]


def _normalized_profile_strength(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        return ()
    ordered = sorted(value for value in values if value > 0.0)
    if not ordered:
        return tuple(0.0 for _ in values)
    p90_index = min(len(ordered) - 1, int((len(ordered) - 1) * 0.9))
    scale = ordered[p90_index]
    if scale <= 1e-12:
        return tuple(0.0 for _ in values)
    return tuple(min(1.0, max(0.0, value / scale)) for value in values)


def _profile_peaks(values: Sequence[float]) -> list[int]:
    peaks: list[int] = []
    index = 1
    while index < len(values) - 1:
        value = values[index]
        if value <= 0.0 or value < values[index - 1]:
            index += 1
            continue
        plateau_end = index
        while plateau_end + 1 < len(values) and values[plateau_end + 1] == value:
            plateau_end += 1
        right = plateau_end + 1
        if right >= len(values) or value >= values[right]:
            peaks.append((index + plateau_end) // 2)
        index = plateau_end + 1
    return peaks


def _profile_strength_at(profile: _PriceProfile, price: float, field: str) -> float:
    values = getattr(profile, field)
    if not values:
        return 0.0
    index = _profile_index(profile.price_min, profile.bin_width, len(values), price)
    return values[index]


def _adaptive_zone_width(
    profile: _PriceProfile,
    cluster_span: float,
    center: float,
    atr: float,
) -> float:
    cluster_width = cluster_span / atr if atr else _MIN_ZONE_WIDTH_ATR
    width = max(_MIN_ZONE_WIDTH_ATR, cluster_width * 0.75)
    strengths = profile.vp_strength
    if strengths:
        center_index = _profile_index(
            profile.price_min,
            profile.bin_width,
            len(strengths),
            center,
        )
        positive = sorted(value for value in strengths if value > 0.0)
        baseline = positive[len(positive) // 2] if positive else 0.0
        threshold = max(0.15, (strengths[center_index] + baseline) / 2.0)
        left = center_index
        right = center_index
        max_half_bins = max(
            1,
            math.ceil(_MAX_ZONE_WIDTH_ATR * atr / max(profile.bin_width * 2.0, 1e-12)),
        )
        while left > 0 and center_index - left < max_half_bins and strengths[left - 1] >= threshold:
            left -= 1
        while (
            right + 1 < len(strengths)
            and right - center_index < max_half_bins
            and strengths[right + 1] >= threshold
        ):
            right += 1
        profile_width = (right - left + 1) * profile.bin_width / atr if atr else 0.0
        width = max(width, profile_width)
    return min(_MAX_ZONE_WIDTH_ATR, max(_MIN_ZONE_WIDTH_ATR, width))


def _build_zones(
    candidates: Sequence[float],
    *,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    current_price: float,
    atr: float,
    atr_values: Sequence[float | None],
    bars_used: int,
    profile: _PriceProfile,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    zones: list[dict[str, Any]] = []
    sorted_candidates = sorted(candidates)
    cluster: list[float] = [sorted_candidates[0]]
    tolerance = atr * 0.7
    for price in sorted_candidates[1:]:
        if price - cluster[0] <= tolerance:
            cluster.append(price)
            continue
        zones.append(
            _zone_from_cluster(
                cluster,
                highs,
                lows,
                closes,
                volumes,
                current_price,
                atr,
                atr_values,
                bars_used,
                profile,
            )
        )
        cluster = [price]
    zones.append(
        _zone_from_cluster(
            cluster,
            highs,
            lows,
            closes,
            volumes,
            current_price,
            atr,
            atr_values,
            bars_used,
            profile,
        )
    )
    return zones


def _zone_from_cluster(
    cluster: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    current_price: float,
    atr: float,
    atr_values: Sequence[float | None],
    bars_used: int,
    profile: _PriceProfile,
) -> dict[str, Any]:
    ordered = sorted(cluster)
    middle = len(ordered) // 2
    center = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
    span = max(ordered) - min(ordered)
    width = _adaptive_zone_width(profile, span, center, atr)
    half_width = width * atr / 2.0
    low = center - half_width
    high = center + half_width

    touch_count = sum(1 for bar_high, bar_low in zip(highs, lows, strict=True) if bar_high >= low and bar_low <= high)
    zone_type = "support" if center < current_price else "resistance"
    event_stats = _touch_event_stats(
        highs,
        lows,
        closes,
        atr_values,
        low,
        high,
        zone_type,
        atr,
    )
    volume_in_zone = 0.0
    total_volume = sum(volumes)
    for bar_high, bar_low, volume in zip(highs, lows, volumes, strict=True):
        if bar_high < low or bar_low > high:
            continue
        overlap = min(bar_high, high) - max(bar_low, low)
        bar_width = max(bar_high - bar_low, 1e-12)
        volume_in_zone += volume * min(1.0, max(0.0, overlap / bar_width))
    volume_pct = volume_in_zone / total_volume * 100.0 if total_volume > 0.0 else 0.0

    distance_atr = abs(center - current_price) / atr
    vp_strength = _profile_strength_at(profile, center, "vp_strength")
    close_strength = _profile_strength_at(profile, center, "close_strength")
    n_events = int(event_stats["n_events"])
    stale = float(event_stats["stale"])
    event_component = min(math.log1p(max(n_events, 0)) / math.log1p(12.0), 1.0)
    edge_score = 100.0 * (0.65 * event_component + 0.35 * stale)
    p_stall = min(
        1.0,
        max(
            0.0,
            (1.0 * vp_strength + 0.8 * close_strength + 0.3 * width) / 2.1,
        ),
    )
    distance_pct = (center - current_price) / current_price * 100.0 if current_price else 0.0
    width_pct = (high - low) / current_price * 100.0 if current_price else None

    return {
        "zone_type": zone_type,
        "zone_label": "支撑带" if zone_type == "support" else "压力带",
        "center": _round(center),
        "low": _round(low),
        "high": _round(high),
        "distance_pct": _round(distance_pct),
        "distance_atr": _round(distance_atr),
        "width_atr": _round(width),
        "width_pct": _round(width_pct, 4) if width_pct is not None else None,
        "edge_score": _round(edge_score, 4),
        "edge_event_component": _round(event_component, 4),
        "edge_stale_component": _round(stale, 4),
        "n_events": n_events,
        "n_decided": int(event_stats["n_decided"]),
        "n_hold": int(event_stats["n_hold"]),
        "event_hold_rate": (
            _round(event_stats["hold_rate"], 4)
            if event_stats["hold_rate"] is not None
            else None
        ),
        "stale": _round(stale, 4),
        "p_stall": _round(p_stall, 4),
        "stall_bucket": _score_bucket(p_stall * 100.0),
        "vp_strength": _round(vp_strength, 4),
        "bucket": _score_bucket(edge_score),
        "bucket_hold_rate": None,
        "bucket_fwd_ret": None,
        "bucket_n": None,
        "p_touch": None,
        "p_hold": None,
        "p_effective": None,
        "volume_pct": _round(volume_pct),
        "touch_count": touch_count,
        "tf_count": 1,
        "tfs": "",
    }


def _touch_event_stats(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    atr_values: Sequence[float | None],
    low: float,
    high: float,
    zone_type: str,
    fallback_atr: float,
) -> dict[str, float | int | None]:
    history_size = max(0, len(closes) - 1)
    empty = {
        "n_events": 0,
        "n_decided": 0,
        "n_hold": 0,
        "hold_rate": None,
        "stale": 1.0,
    }
    if history_size <= 1:
        return empty

    event_indices: list[int] = []
    last_event = -_TOUCH_GAP_BARS
    for index in range(1, history_size):
        intersects = highs[index] >= low and lows[index] <= high
        if not intersects:
            continue
        previous_close = closes[index - 1]
        valid_direction = (
            previous_close > high if zone_type == "support" else previous_close < low
        )
        if not valid_direction or index - last_event < _TOUCH_GAP_BARS:
            continue
        event_indices.append(index)
        last_event = index

    if not event_indices:
        return empty

    n_hold = 0
    n_decided = 0
    for index in event_indices:
        end = min(history_size - 1, index + _TOUCH_HOLD_BARS)
        if end <= index:
            continue
        event_atr = atr_values[index] or fallback_atr
        event_atr = max(event_atr, 1e-12)
        hold_index: int | None = None
        break_index: int | None = None
        for outcome_index in range(index + 1, end + 1):
            if zone_type == "support":
                reached_target = highs[outcome_index] >= high + _TOUCH_TARGET_ATR * event_atr
                broke_zone = closes[outcome_index] < low - _TOUCH_BREAK_ATR * event_atr
            else:
                reached_target = lows[outcome_index] <= low - _TOUCH_TARGET_ATR * event_atr
                broke_zone = closes[outcome_index] > high + _TOUCH_BREAK_ATR * event_atr
            if reached_target and hold_index is None:
                hold_index = outcome_index
            if broke_zone and break_index is None:
                break_index = outcome_index
            if hold_index is not None or break_index is not None:
                break

        if hold_index is None and break_index is None:
            continue
        n_decided += 1
        if break_index is None or (hold_index is not None and hold_index <= break_index):
            n_hold += 1

    age = history_size - 1 - event_indices[-1]
    recency = 2.0 ** (-age / _STALE_HALF_LIFE)
    return {
        "n_events": len(event_indices),
        "n_decided": n_decided,
        "n_hold": n_hold,
        "hold_rate": n_hold / n_decided if n_decided else None,
        "stale": min(1.0, max(0.0, 1.0 - recency)),
    }


def _score_bucket(score: float) -> str:
    if score < 25.0:
        return "Q1"
    if score < 50.0:
        return "Q2"
    if score < 75.0:
        return "Q3"
    return "Q4"


def _mark_higher_timeframe_resonance(
    zones: list[dict[str, Any]],
    bars: Sequence[dict[str, float]],
    timeframe: str,
    atr: float,
) -> list[dict[str, Any]]:
    higher_frames = _higher_timeframe_frames(timeframe, bars)
    for zone in zones:
        resonances: set[str] = set()
        for label, frame in higher_frames:
            frame_highs = [item["high"] for item in frame]
            frame_lows = [item["low"] for item in frame]
            for price in _swing_pivot_candidates(frame_highs, frame_lows):
                if abs(price - zone["center"]) <= atr * 0.8:
                    resonances.add(label)
                    break
        if resonances:
            zone["tfs"] = "+".join(sorted(resonances))
            zone["tf_count"] = 1 + len(resonances)
    return zones


def _higher_timeframe_frames(
    timeframe: str,
    bars: Sequence[dict[str, float]],
) -> list[tuple[str, list[dict[str, float]]]]:
    key = str(timeframe).strip().lower()
    if key in {"1d", "d1", "day", "daily", "1day"}:
        frames = [("1w", 5)]
    elif key in {"1h", "h1", "hour", "hourly", "60m"}:
        frames = [("4h", 4), ("1d", 24)]
    elif key in {"1w", "w1", "week", "weekly"}:
        frames = [("1m", 4)]
    else:
        frames = [(f"5x-{key or 'base'}", 5)]

    result: list[tuple[str, list[dict[str, float]]]] = []
    for label, group_size in frames:
        if len(bars) // group_size < 20:
            continue
        start = len(bars) % group_size
        grouped: list[dict[str, float]] = []
        for offset in range(start, len(bars), group_size):
            chunk = bars[offset : offset + group_size]
            grouped.append(
                {
                    "open": chunk[0]["open"],
                    "high": max(item["high"] for item in chunk),
                    "low": min(item["low"] for item in chunk),
                    "close": chunk[-1]["close"],
                    "volume": sum(item["volume"] for item in chunk),
                }
            )
        result.append((label, grouped))
    return result


def _filter_and_limit(
    zones: list[dict[str, Any]],
    current_price: float,
    atr: float,
    n_zones: int,
    direction: str,
) -> list[dict[str, Any]]:
    normalized_direction = _normalized_direction(direction)
    wanted_types = {"support", "resistance"}
    if normalized_direction == "long":
        wanted_types = {"support"}
    elif normalized_direction == "short":
        wanted_types = {"resistance"}

    filtered_by_type: dict[str, list[dict[str, Any]]] = {"support": [], "resistance": []}
    for zone in zones:
        if zone["zone_type"] not in wanted_types:
            continue
        if zone["zone_type"] == "support" and (zone["center"] >= current_price or zone["high"] >= current_price):
            continue
        if zone["zone_type"] == "resistance" and (zone["center"] <= current_price or zone["low"] <= current_price):
            continue
        distance_atr = abs(zone["center"] - current_price) / atr
        if distance_atr < _MIN_DISTANCE_ATR or distance_atr > _MAX_DISTANCE_ATR:
            continue
        filtered_by_type[zone["zone_type"]].append(zone)

    per_side = max(1, _bounded_zone_count(n_zones) // 2)
    limited: list[dict[str, Any]] = []
    for zone_type in ("support", "resistance"):
        ranked = sorted(
            filtered_by_type[zone_type],
            key=lambda item: (-item["edge_score"], abs(item["center"] - current_price), item["center"]),
        )
        limited.extend(ranked[:per_side])
    limited.sort(key=lambda item: (item["zone_type"], abs(item["distance_atr"])))
    return limited


def _trend(closes: Sequence[float]) -> dict[str, str]:
    if len(closes) < 20:
        return {"label": "震荡", "detail": "样本不足，按震荡处理"}
    alpha = 2.0 / 21.0
    ema_values = [closes[0]]
    ema = closes[0]
    for price in closes[1:]:
        ema = alpha * price + (1.0 - alpha) * ema
        ema_values.append(ema)
    previous_ema = ema_values[-6]
    deviation = (closes[-1] - ema) / ema * 100.0 if ema else 0.0
    slope = (ema - previous_ema) / previous_ema * 100.0 if previous_ema else 0.0
    trend_score = deviation * 0.6 + slope * 0.4
    label = "上涨" if trend_score > 1.5 else "下跌" if trend_score < -1.5 else "震荡"
    detail = f"现价相对EMA20 {deviation:+.2f}%，EMA20五根斜率 {slope:+.2f}%"
    return {"label": label, "detail": detail}


def _build_summary(
    levels: Sequence[dict[str, Any]],
    trend: dict[str, str],
    current_price: float,
    atr: float,
    direction: str,
) -> dict[str, Any]:
    normalized_direction = _normalized_direction(direction)
    candidates = list(levels)
    nearest = min(candidates, key=lambda item: abs(item["distance_atr"])) if candidates else None
    best = max(candidates, key=lambda item: item["edge_score"]) if candidates else None

    supports = [item for item in candidates if item["zone_type"] == "support"]
    resistances = [item for item in candidates if item["zone_type"] == "resistance"]
    nearest_support = min(supports, key=lambda item: abs(item["center"] - current_price)) if supports else None
    nearest_resistance = (
        min(resistances, key=lambda item: abs(item["center"] - current_price)) if resistances else None
    )
    reward_distance = abs(nearest_resistance["center"] - current_price) if nearest_resistance else atr * 2.0
    risk_distance = abs(nearest_support["center"] - current_price) if nearest_support else atr
    ratio = reward_distance / risk_distance if risk_distance > 0.0 else 0.0
    if ratio >= 3.0:
        quality = "优秀"
    elif ratio >= 2.0:
        quality = "良好"
    elif ratio >= 1.0:
        quality = "一般"
    else:
        quality = "较差"

    if nearest is None:
        headline = "距离带内没有符合条件的支撑阻力位"
    else:
        type_label = nearest.get("zone_label") or (
            "支撑带" if nearest["zone_type"] == "support" else "压力带"
        )
        headline = (
            f"趋势{trend['label']}，最近{type_label} {_round(nearest['center'])}"
            f"（距离 {nearest['distance_pct']:+.2f}% / {nearest['distance_atr']:.2f} ATR）"
        )

    return {
        "headline": headline,
        "nearest": dict(nearest) if nearest else None,
        "best": dict(best) if best else None,
        "risk_reward": {
            "ratio": _round(ratio, 4),
            "quality": quality,
            "potential_reward_atr": _round(reward_distance / atr, 4),
            "potential_risk_atr": _round(risk_distance / atr, 4),
            "basis": "nearest_levels" if nearest_support and nearest_resistance else "fallback",
        },
        "caveat": _CAVEAT,
        "direction": normalized_direction,
        "direction_label": _DIRECTION_LABELS[normalized_direction],
        "trend_label": trend["label"],
        "trend_detail": trend["detail"],
    }


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)

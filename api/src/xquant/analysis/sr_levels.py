from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

_MIN_BARS = 60
_MIN_DISTANCE_ATR = 0.5
_MAX_DISTANCE_ATR = 5.0

_CAVEAT = (
    "支撑阻力是规则化统计区域，不预测未来价格；"
    "结果仅用于研究和模拟，不构成投资建议。"
)


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
    atr = _atr14(highs, lows, closes) or max(abs(current_price) * 1e-3, 1e-9)

    candidates = _swing_pivot_candidates(highs, lows)
    candidates.extend(_volume_density_candidates(highs, lows, volumes, atr))
    zones = _build_zones(
        candidates,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=current_price,
        atr=atr,
        bars_used=bars_used,
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
            "engine": "xq_sr_fusion_v1",
            "simulation_only": True,
            "lookback": _bounded_lookback(lookback),
            "n_zones": _bounded_zone_count(n_zones),
            "direction": _normalized_direction(direction),
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


def _atr14(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> float | None:
    if len(closes) < 14:
        return None
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
    result = sum(true_ranges[:14]) / 14.0
    for index in range(14, len(true_ranges)):
        result = (result * 13.0 + true_ranges[index]) / 14.0
    return result if math.isfinite(result) and result > 0.0 else None


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
) -> list[float]:
    price_min = min(lows)
    price_max = max(highs)
    if price_max <= price_min:
        return [price_min]

    target_bins = min(180, max(20, int((price_max - price_min) / max(atr * 0.5, 1e-12)) + 1))
    bin_width = (price_max - price_min) / target_bins
    density = [0.0] * target_bins
    for high, low, volume in zip(highs, lows, volumes, strict=True):
        if volume <= 0.0:
            continue
        bar_width = max(high - low, bin_width * 1e-6)
        first = min(target_bins - 1, max(0, int((low - price_min) / bin_width)))
        last = min(target_bins - 1, max(0, int((high - price_min) / bin_width)))
        for bin_index in range(first, last + 1):
            bin_low = price_min + bin_index * bin_width
            bin_high = bin_low + bin_width
            overlap = min(high, bin_high) - max(low, bin_low)
            if overlap > 0.0:
                density[bin_index] += volume * overlap / bar_width

    smoothed = [
        sum(density[max(0, index - 1) : min(target_bins, index + 2)])
        / len(density[max(0, index - 1) : min(target_bins, index + 2)])
        for index in range(target_bins)
    ]
    peaks = [
        index
        for index in range(1, target_bins - 1)
        if smoothed[index] > 0.0
        and smoothed[index] >= smoothed[index - 1]
        and smoothed[index] >= smoothed[index + 1]
    ]
    if not peaks:
        peaks = [max(range(target_bins), key=lambda index: smoothed[index])]

    selected: list[int] = []
    minimum_separation = max(1, int(0.75 * atr / bin_width))
    for index in sorted(peaks, key=lambda item: smoothed[item], reverse=True):
        if all(abs(index - chosen) >= minimum_separation for chosen in selected):
            selected.append(index)
        if len(selected) == 10:
            break
    return sorted(price_min + (index + 0.5) * bin_width for index in selected)


def _build_zones(
    candidates: Sequence[float],
    *,
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    current_price: float,
    atr: float,
    bars_used: int,
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
        zones.append(_zone_from_cluster(cluster, highs, lows, volumes, current_price, atr, bars_used))
        cluster = [price]
    zones.append(_zone_from_cluster(cluster, highs, lows, volumes, current_price, atr, bars_used))
    return zones


def _zone_from_cluster(
    cluster: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    volumes: Sequence[float],
    current_price: float,
    atr: float,
    bars_used: int,
) -> dict[str, Any]:
    ordered = sorted(cluster)
    middle = len(ordered) // 2
    center = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
    span = max(ordered) - min(ordered)
    width = min(1.2, max(0.3, min(0.8, span / atr if atr else 0.8)))
    half_width = width * atr / 2.0
    low = center - half_width
    high = center + half_width

    touch_count = sum(1 for bar_high, bar_low in zip(highs, lows, strict=True) if bar_high >= low and bar_low <= high)
    n_events = _contact_runs(highs, lows, low, high)
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
    proximity = max(0.0, 1.0 - min(distance_atr / _MAX_DISTANCE_ATR, 1.0))
    volume_component = min(volume_pct / 15.0, 1.0)
    touch_component = min(touch_count / max(12.0, bars_used * 0.08), 1.0)
    edge_score = 100.0 * (0.45 * volume_component + 0.35 * touch_component + 0.20 * proximity)
    distance_pct = (center - current_price) / current_price * 100.0 if current_price else 0.0
    zone_type = "support" if center < current_price else "resistance"

    return {
        "zone_type": zone_type,
        "center": _round(center),
        "low": _round(low),
        "high": _round(high),
        "distance_pct": _round(distance_pct),
        "distance_atr": _round(distance_atr),
        "width_atr": _round(width),
        "edge_score": _round(edge_score, 4),
        "n_events": n_events,
        "volume_pct": _round(volume_pct),
        "touch_count": touch_count,
        "tf_count": 1,
        "tfs": "",
    }


def _contact_runs(
    highs: Sequence[float],
    lows: Sequence[float],
    low: float,
    high: float,
) -> int:
    runs = 0
    previous_touch = False
    for bar_high, bar_low in zip(highs, lows, strict=True):
        touch = bar_high >= low and bar_low <= high
        if touch and not previous_touch:
            runs += 1
        previous_touch = touch
    return runs


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
        type_label = "支撑" if nearest["zone_type"] == "support" else "阻力"
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
    }


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)

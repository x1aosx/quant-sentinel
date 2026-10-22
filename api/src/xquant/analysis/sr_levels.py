"""支撑/阻力位检测入口（线上适配层）

算法本体在 :mod:`xquant.analysis.density_sr` —— 移植自
``Detect_support_and_resistance_levels`` 的 V3 融合引擎（体积守恒成交量剖面 +
因果 ZigZag 枢轴 + ATR 归一化 + 分位档标定 + 双概率模型）。

算法与参数（V3Fusion / 双概率 / 分位档标定）全部与上游保持一致，未做任何本地调整。

本模块只负责三件事：

1. 把项目里的 K 线规范成引擎可接受的输入（字段别名、数值字符串、缺失成交量）
2. 调用引擎并做方向过滤
3. 拼装本项目既有的返回结构（``levels`` / ``summary`` / ``meta``），供
   ``/analysis/support-resistance``、AI 快照、单周期分析器复用
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from xquant.analysis.density_sr import (
    DIRECTION_LABELS,
    MIN_BARS,
    SREngine,
    attach_mtf,
    build_summary,
    frame_from_bars,
)
from xquant.analysis.density_sr.detectors import V3Params

_MIN_LOOKBACK = 60
_MAX_LOOKBACK = 1000
_MIN_ZONES = 2
_MAX_ZONES = 12
_DIRECTIONS = {"long", "short", "both"}

_RR_QUALITY: tuple[tuple[float, str], ...] = (
    (3.0, "优秀"),
    (2.0, "良好"),
    (1.0, "一般"),
)


def detect_support_resistance(
    bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str = "",
    timeframe: str = "1d",
    lookback: int = 250,
    n_zones: int = 6,
    direction: str = "both",
    mtf_frames: Sequence[tuple[str, Sequence[Mapping[str, Any]]]] | None = None,
) -> dict[str, Any]:
    """检测支撑/阻力带并给出双概率与多周期共振。

    ``lookback`` 是成交量剖面的回看长度（与上游 ``V3Params.lookback`` 同义），
    历史触及统计仍会使用调用方传入的全部 K 线；``mtf_frames`` 是本品种其他
    周期的 K 线，用于标注共振（没有就退化为单周期）。
    """
    normalized = _normalize_bars(bars)
    frame = frame_from_bars(normalized, symbol=symbol, timeframe=timeframe)
    if len(frame) < MIN_BARS:
        raise ValueError("数据不足：至少需要 60 根K线")

    bounded_lookback = _bounded_lookback(lookback)
    bounded_zones = _bounded_zone_count(n_zones)
    normalized_direction = _normalized_direction(direction)

    engine = SREngine(params=V3Params(lookback=bounded_lookback), n_zones=bounded_zones)
    zones, info = engine.detect(frame, symbol)
    if info.get("error"):
        raise ValueError(str(info["error"]))

    if normalized_direction != "both":
        focus = "support" if normalized_direction == "long" else "resistance"
        zones = [zone for zone in zones if zone["zone_type"] == focus]

    mtf_zones = _mtf_zones(mtf_frames, symbol=symbol, timeframe=timeframe, n_zones=bounded_zones)
    if mtf_zones:
        attach_mtf(zones, mtf_zones, float(info.get("atr") or 0.0))

    summary = build_summary(zones, frame, normalized_direction, info)
    current_price = float(frame["close"].iloc[-1])
    atr = float(info.get("atr") or 0.0)
    summary["risk_reward"] = _risk_reward(zones, current_price, atr)
    summary["direction"] = normalized_direction
    summary["direction_label"] = DIRECTION_LABELS[normalized_direction]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_used": min(bounded_lookback, len(frame)),
        "current_price": _round(current_price),
        "atr": _round(atr),
        "atr_pct": _round(atr / current_price * 100.0 if current_price else 0.0),
        "trend": {"label": summary["trend_label"], "detail": summary["trend_detail"]},
        "levels": zones,
        "summary": summary,
        "mtf_zones": mtf_zones,
        "meta": {
            "engine": info.get("engine", "V3_fusion"),
            "simulation_only": True,
            "lookback": bounded_lookback,
            "n_zones": bounded_zones,
            "direction": normalized_direction,
            "direction_label": DIRECTION_LABELS[normalized_direction],
            "calibrated": bool(info.get("calibrated")),
            "calibration_meta": info.get("calib_meta") or {},
            "prob_ready": bool(info.get("prob_ready")),
            "probability_model": info.get("prob_meta") or None,
            "adjust_mode": info.get("adjust_mode"),
            "adjust_source": info.get("adjust_source"),
            "mtf_timeframes": [item["tf"] for item in mtf_zones],
        },
    }


def _mtf_zones(
    mtf_frames: Sequence[tuple[str, Sequence[Mapping[str, Any]]]] | None,
    *,
    symbol: str,
    timeframe: str,
    n_zones: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for tf, bars in mtf_frames or []:
        if str(tf) == str(timeframe):
            continue
        try:
            frame = frame_from_bars(bars, symbol=symbol, timeframe=str(tf))
        except (TypeError, ValueError):
            continue
        if len(frame) < MIN_BARS:
            continue
        zones, info = SREngine(n_zones=n_zones).detect(frame, symbol)
        if info.get("error"):
            continue
        output.append({"tf": str(tf), "zones": zones})
    return output


def _risk_reward(
    levels: Sequence[Mapping[str, Any]],
    current_price: float,
    atr: float,
) -> dict[str, Any]:
    """用最近的支撑/压力给出一个粗略的风险回报参考（沿用改造前的字段）。"""
    safe_atr = atr if atr > 0.0 else max(abs(current_price) * 1e-3, 1e-9)
    supports = [level for level in levels if level["zone_type"] == "support"]
    resistances = [level for level in levels if level["zone_type"] == "resistance"]
    nearest_support = (
        min(supports, key=lambda level: abs(float(level["center"]) - current_price))
        if supports
        else None
    )
    nearest_resistance = (
        min(resistances, key=lambda level: abs(float(level["center"]) - current_price))
        if resistances
        else None
    )
    reward_distance = (
        abs(float(nearest_resistance["center"]) - current_price)
        if nearest_resistance
        else safe_atr * 2.0
    )
    risk_distance = (
        abs(float(nearest_support["center"]) - current_price) if nearest_support else safe_atr
    )
    ratio = reward_distance / risk_distance if risk_distance > 0.0 else 0.0
    quality = "较差"
    for threshold, label in _RR_QUALITY:
        if ratio >= threshold:
            quality = label
            break
    return {
        "ratio": _round(ratio, 4),
        "quality": quality,
        "potential_reward_atr": _round(reward_distance / safe_atr, 4),
        "potential_risk_atr": _round(risk_distance / safe_atr, 4),
        "basis": "nearest_levels" if nearest_support and nearest_resistance else "fallback",
    }


def _normalize_bars(bars: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if not isinstance(bar, Mapping):
            raise TypeError(f"第 {index + 1} 根K线不是映射对象")
        try:
            open_price = _as_float(_field(bar, ("open", "o")), "open")
            high = _as_float(_field(bar, ("high", "h")), "high")
            low = _as_float(_field(bar, ("low", "l")), "low")
            close = _as_float(_field(bar, ("close", "c")), "close")
            volume = _as_float(_field_or_default(bar, ("volume", "vol", "v"), 0.0), "volume")
        except KeyError as exc:
            detail = exc.args[0] if exc.args else "缺少字段"
            raise ValueError(f"第 {index + 1} 根K线数值无效：{detail}") from exc
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


def _field_or_default(bar: Mapping[str, Any], names: tuple[str, ...], default: Any) -> Any:
    """Return the first present field, or ``default`` when every alias is absent.

    Missing volume degrades to 0.0, matching ``price_action._parse_bars``.
    """
    try:
        return _field(bar, names)
    except KeyError:
        return default


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
        return 250
    return min(_MAX_LOOKBACK, max(_MIN_LOOKBACK, value))


def _bounded_zone_count(n_zones: int) -> int:
    try:
        value = int(n_zones)
    except (TypeError, ValueError):
        return 6
    return min(_MAX_ZONES, max(_MIN_ZONES, value))


def _normalized_direction(direction: str) -> str:
    value = str(direction).strip().lower()
    return value if value in _DIRECTIONS else "both"


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def int_in(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def number_in(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_bars(raw_bars: Any) -> list[dict[str, Any]]:
    if isinstance(raw_bars, dict):
        raw_bars = raw_bars.get("bars")
    if not isinstance(raw_bars, list):
        raise TypeError("bars 必须是数组")
    if len(raw_bars) > 10_000:
        raise ValueError("单次导入不能超过 10,000 根K线")

    bars: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_bars):
        if not isinstance(raw, dict):
            raise TypeError(f"第 {index + 1} 根K线格式错误")
        try:
            open_ = float(raw["open"])
            high = float(raw["high"])
            low = float(raw["low"])
            close = float(raw["close"])
            volume = float(raw.get("volume", raw.get("tick_volume", 0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index + 1} 根K线的 OHLCV 无效") from exc
        if min(open_, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"第 {index + 1} 根K线价格必须大于 0，成交量不能小于 0")
        if low > min(open_, close) or high < max(open_, close):
            raise ValueError(f"第 {index + 1} 根K线高低价与开收盘价冲突")
        session = raw.get("session_id", raw.get("session", raw.get("time", raw.get("date"))))
        bars.append(
            {
                "session_id": str(session or f"S{index + 1:04d}"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return bars


def required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{field_name}不能为空")
    return text

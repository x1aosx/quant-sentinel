"""
数据装配层：本项目数据集 -> 支撑阻力引擎所需的 DataFrame

上游项目 Detect_support_and_resistance_levels 的 ``srlab.data`` 直接读取本地
parquet 文件夹（并自带前复权/后复权因子缓存）。本仓库的数据来源已经统一在
``xquant.registry`` 里（数据库中的 research.dataset + 行情 K 线），复权口径由
数据同步端负责，因此这里把"读文件"替换成"装配数据集 K 线"，其余口径
（因果 ATR、tick 估计、多周期层级）与上游保持一致。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

MIN_BARS = 60

# 周期层级：从最小到最大，用于多周期共振里挑选"邻居周期"
TF_ORDER: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")

TIMEFRAME_LABELS: dict[str, str] = {
    "1m": "1分钟",
    "5m": "5分钟",
    "15m": "15分钟",
    "30m": "30分钟",
    "1h": "1小时",
    "4h": "4小时",
    "1d": "日线",
    "1w": "周线",
}

# 每个周期默认分析窗口（K线数）：周期越大，窗口越小，覆盖相近的日历跨度
TF_WINDOW: dict[str, int] = {
    "1m": 120,
    "5m": 100,
    "15m": 80,
    "30m": 60,
    "1h": 60,
    "4h": 40,
    "1d": 40,
    "1w": 30,
}

_BAR_NUMBER_FIELDS = ("open", "high", "low", "close", "volume")


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_c = np.concatenate([[close[0]], close[:-1]])
    return np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)),
    )


def atr_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    因果 ATR（Wilder 平滑的简化版：滚动均值）。
    第 i 个元素仅使用 0..i 的数据，可安全用于 t 时刻决策。
    前 period 根用累计均值填充，不产生 NaN。
    """
    tr = true_range(high, low, close)
    n = len(tr)
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    csum = np.cumsum(tr)
    for i in range(min(period, n)):
        out[i] = csum[i] / (i + 1)
    if n > period:
        roll = csum[period:] - np.concatenate([[0.0], csum[: n - period - 1]])
        out[period:] = roll / period
    return out


def tick_size_guess(close: np.ndarray) -> float:
    """
    估计最小价格变动单位。A股为 0.01 元；对高价品种放宽。
    仅用于给分箱宽度设下限，避免箱宽小于可成交精度。
    """
    px = float(np.nanmedian(close))
    if px <= 0:
        return 0.01
    if px < 1000:
        return 0.01
    return 10 ** (np.floor(np.log10(px)) - 4)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def frame_from_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    symbol: str = "",
    timeframe: str = "1d",
    adjust_mode: str = "unknown",
    adjust_source: str = "unknown",
) -> pd.DataFrame:
    """
    把数据集 K 线装配成引擎使用的 DataFrame。

    - 列：date / open / high / low / close / volume
    - 同一 session 只保留最后一根；缺失或不合法（非有限、high<low、非正价）的 K 线丢弃
    - ``df.attrs`` 带上 tick 基准与复权口径，供引擎原样回传给前端
    """
    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for bar in bars:
        if not isinstance(bar, Mapping):
            continue
        values = {field: _number(bar.get(field)) for field in _BAR_NUMBER_FIELDS}
        if not all(np.isfinite(value) for value in values.values()):
            continue
        if min(values["high"], values["low"], values["close"]) <= 0:
            continue
        if values["high"] < values["low"]:
            continue
        session = str(bar.get("session_id") or bar.get("date") or len(rows))
        record = {
            "date": session,
            "open": values["open"] if values["open"] > 0 else values["close"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "volume": max(values["volume"], 0.0),
        }
        previous = seen.get(session)
        if previous is None:
            seen[session] = len(rows)
            rows.append(record)
        else:
            rows[previous] = record

    frame = pd.DataFrame(
        rows,
        columns=["date", "open", "high", "low", "close", "volume"],
    )
    if not frame.empty:
        frame["open"] = frame["open"].astype(float)
        frame["high"] = frame["high"].astype(float)
        frame["low"] = frame["low"].astype(float)
        frame["close"] = frame["close"].astype(float)
        frame["volume"] = frame["volume"].astype(float)
    frame.attrs["symbol"] = symbol
    frame.attrs["timeframe"] = timeframe
    frame.attrs["adjust_mode"] = adjust_mode
    frame.attrs["adjust_source"] = adjust_source
    frame.attrs["tick_base"] = (
        tick_size_guess(frame["close"].to_numpy(dtype=np.float64)) if not frame.empty else 0.01
    )
    return frame


def normalize_timeframe(timeframe: str) -> str:
    return str(timeframe or "1d").strip().lower()


def timeframe_sort_key(timeframe: str) -> tuple[int, str]:
    normalized = normalize_timeframe(timeframe)
    try:
        return (TF_ORDER.index(normalized), normalized)
    except ValueError:
        return (len(TF_ORDER), normalized)


def pick_mtf_timeframes(
    main_tf: str,
    available_tfs: Sequence[str],
    up: int = 2,
    down: int = 2,
) -> list[str]:
    """
    自适应挑选多周期共振的邻居周期。

    按周期层级向上（更大周期，结构更重要）最多取 ``up`` 个、向下（更小周期）
    最多取 ``down`` 个可用邻居，返回的列表不含主周期本身。
    """
    main = normalize_timeframe(main_tf)
    available = {normalize_timeframe(tf) for tf in available_tfs}
    if main not in TF_ORDER:
        return []
    index = TF_ORDER.index(main)
    picked: list[str] = []

    position = index + 1
    count = 0
    while position < len(TF_ORDER) and count < up:
        candidate = TF_ORDER[position]
        if candidate in available:
            picked.append(candidate)
            count += 1
        position += 1

    position = index - 1
    count = 0
    while position >= 0 and count < down:
        candidate = TF_ORDER[position]
        if candidate in available:
            picked.append(candidate)
            count += 1
        position -= 1
    return picked

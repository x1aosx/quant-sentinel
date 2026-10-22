"""
支撑/阻力位检测引擎（移植自 Detect_support_and_resistance_levels 的 V3 融合算法）

模块结构：
    base        因果上下文 Ctx / 关键位 Level / 接口约定
    profile     体积守恒的成交量剖面、点剖面、稳健标准化
    pivots      因果 ZigZag 枢轴（带 confirm_idx，避免未来信息）
    detectors   V1 基线 / V2 融合 / V3 融合（线上使用 V3）
    probability 双概率模型（触及 + 守住）的逻辑回归容器
    metrics     Wilson 区间
    data        数据集 K 线 -> DataFrame 装配层（替代上游的 parquet 文件夹读取）
    engine      线上适配层：检测 -> JSON、多周期共振、方向化摘要

数据来源是 xquant 数据集（research.dataset），不是上游的本地 parquet 文件夹。
"""

from __future__ import annotations

from .data import (
    MIN_BARS,
    TF_ORDER,
    TF_WINDOW,
    TIMEFRAME_LABELS,
    atr_series,
    frame_from_bars,
    normalize_timeframe,
    pick_mtf_timeframes,
    tick_size_guess,
    timeframe_sort_key,
    true_range,
)
from .engine import (
    CALIB_PATH,
    DIRECTION_LABELS,
    MAX_DIST_ATR,
    MIN_DIST_ATR,
    PROB_PATH,
    Calibration,
    SREngine,
    attach_mtf,
    build_summary,
    get_calibration,
    get_prob_models,
    trend_of,
)

__all__ = [
    "CALIB_PATH",
    "DIRECTION_LABELS",
    "MAX_DIST_ATR",
    "MIN_BARS",
    "MIN_DIST_ATR",
    "PROB_PATH",
    "TF_ORDER",
    "TF_WINDOW",
    "TIMEFRAME_LABELS",
    "Calibration",
    "SREngine",
    "atr_series",
    "attach_mtf",
    "build_summary",
    "frame_from_bars",
    "get_calibration",
    "get_prob_models",
    "normalize_timeframe",
    "pick_mtf_timeframes",
    "tick_size_guess",
    "timeframe_sort_key",
    "trend_of",
    "true_range",
]

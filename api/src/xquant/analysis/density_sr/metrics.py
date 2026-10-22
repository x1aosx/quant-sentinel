"""
统计工具（线上运行时部分）

上游 Detect_support_and_resistance_levels 的 ``srlab.metrics`` 还包含
走查回测用的汇总/显著性/等渗校准工具（summarize、compare、cmh_test、
IsotonicCalibrator、backtest_touch_strategy 等）。那些函数依赖 parquet
形式的走查落盘结果，本仓库不生产该类数据，因此这里只保留线上检测器真正
用到的 Wilson 区间。
"""

from __future__ import annotations

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson 得分区间。返回 (点估计, 下界, 上界)，n=0 时全 nan"""
    if n <= 0:
        return (np.nan, np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))

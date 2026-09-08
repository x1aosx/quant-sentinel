from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Level:
    level_id: str
    detector_version: str
    known_at: str
    low: float
    high: float
    center: float
    kind: str
    location_score: float
    confirmed_pivot_count: int
    historical_resolved_count: int
    stall_score: float
    provenance: str


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    if len(values) < span:
        return np.full(len(values), np.nan)
    result = np.empty_like(values, dtype=float)
    seed = float(np.mean(values[:span]))
    result[span - 1] = seed
    alpha = 2.0 / (span + 1.0)
    prev = seed
    for i in range(span, len(values)):
        prev = alpha * float(values[i]) + (1.0 - alpha) * prev
        result[i] = prev
    return result


def detect_levels(
    df,
    *,
    detector_version: str = "xq_sr_v01",
    lookback: int = 250,
    atr_col: str = "atr14",
    zone_width_atr: float = 0.6,
    max_zones_per_side: int = 3,
) -> list[Level]:
    """Deterministic, causal SR region detector based on confirmed fractals + ATR bins."""
    if df.empty:
        return []
    tail = df.tail(lookback).reset_index(drop=True)
    close = tail["close"].to_numpy(dtype=float)
    high = tail["high"].to_numpy(dtype=float)
    low = tail["low"].to_numpy(dtype=float)
    atr = tail[atr_col].ffill().to_numpy(dtype=float)
    atr = np.where(np.isfinite(atr) & (atr > 0), atr, np.nanmedian(atr[atr > 0]) if np.any(atr > 0) else 1.0)
    current = float(close[-1])
    current_atr = float(atr[-1])
    pivots: list[tuple[str, float, int]] = []
    for i in range(2, len(tail) - 2):
        if high[i] > high[i - 1] and high[i] > high[i - 2] and high[i] > high[i + 1] and high[i] > high[i + 2]:
            pivots.append(("resistance", float(high[i]), i + 2))
        if low[i] < low[i - 1] and low[i] < low[i - 2] and low[i] < low[i + 1] and low[i] < low[i + 2]:
            pivots.append(("support", float(low[i]), i + 2))
    levels: list[Level] = []
    for kind, price, confirmed_idx in pivots:
        center = price
        half = zone_width_atr * current_atr / 2.0
        low_b = center - half
        high_b = center + half
        confirmed = sum(1 for k, p, _ in pivots if k == kind and low_b <= p <= high_b)
        historical = sum(1 for k, p, _ in pivots if k == kind and low_b <= p <= high_b and confirmed_idx < len(tail))
        score = float(np.exp(-abs(center - current) / (current_atr * 3.0)))
        stall = float(0.5 + 0.3 * (high_b - low_b) / current_atr)
        levels.append(
            Level(
                level_id=f"{detector_version}:{kind}:{confirmed_idx}:{center:.4f}",
                detector_version=detector_version,
                known_at=str(tail.loc[confirmed_idx, "session_id"]) if confirmed_idx < len(tail) else "",
                low=low_b,
                high=high_b,
                center=center,
                kind=kind,
                location_score=score,
                confirmed_pivot_count=confirmed,
                historical_resolved_count=historical,
                stall_score=stall,
                provenance="synthetic_reference",
            )
        )
    levels.sort(key=lambda lv: (lv.kind, abs(lv.center - current), -lv.location_score, lv.level_id))
    result: list[Level] = []
    for kind in ("support", "resistance"):
        count = 0
        for lv in levels:
            if lv.kind != kind:
                continue
            if count >= max_zones_per_side:
                continue
            result.append(lv)
            count += 1
    return result


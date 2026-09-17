from __future__ import annotations

import numpy as np

from ..config import MiningSettings
from ..domain import Direction, SignalDecision, SignalState


class SignalKernel:
    """The single authority for position, direction, and strength semantics."""

    version = "tanh_continuous_v1"

    def __init__(self, settings: MiningSettings | None = None) -> None:
        self.settings = settings or MiningSettings()

    def positions(self, factors: np.ndarray) -> np.ndarray:
        positions = np.tanh(factors)
        threshold = float(self.settings.min_exposure)
        if threshold > 0:
            positions = np.where(np.abs(positions) >= threshold, positions, 0.0)
        return positions

    def direction(self, position: float) -> Direction:
        if position >= self.settings.min_exposure:
            return Direction.LONG
        if position <= -self.settings.min_exposure:
            return Direction.SHORT
        return Direction.FLAT

    def evaluate_last(
        self,
        *,
        factors: np.ndarray,
        strategy_id: str,
        strategy_version: str,
        formula_tokens: tuple[int, ...],
        factor_schema_version: str,
        bar_time: str,
        data_snapshot_id: str = "",
    ) -> SignalDecision:
        if factors.ndim != 2 or factors.shape[1] == 0:
            raise ValueError("factor output must have shape [N, T] with T > 0")
        factor_value = float(factors[0, -1])
        if not np.isfinite(factor_value):
            raise ValueError("factor value must be finite")
        position = float(np.tanh(factor_value))
        if abs(position) < self.settings.min_exposure:
            position = 0.0
        return SignalDecision(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            formula_tokens=tuple(int(token) for token in formula_tokens),
            factor_schema_version=factor_schema_version,
            direction=self.direction(position),
            position=round(position, 8),
            strength=round(abs(position), 8),
            factor_value=round(factor_value, 8),
            bars_used=int(factors.shape[1]),
            bar_time=bar_time,
            data_snapshot_id=data_snapshot_id,
            state=SignalState.OK,
        )

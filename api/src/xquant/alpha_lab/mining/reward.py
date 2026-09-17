from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np

from ..data import BarFrame
from ..factor import SignalKernel


def _finite_mask(*arrays: np.ndarray) -> np.ndarray:
    mask = np.ones(arrays[0].shape, dtype=bool)
    for array in arrays:
        if array.shape != mask.shape:
            raise ValueError("reward inputs must share the same shape")
        mask &= np.isfinite(array)
    return mask


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    mask = _finite_mask(x, y)
    if int(mask.sum()) < 2:
        return 0.0
    values_x = np.asarray(x[mask], dtype=np.float64)
    values_y = np.asarray(y[mask], dtype=np.float64)
    values_x -= float(values_x.mean())
    values_y -= float(values_y.mean())
    denominator = float(np.linalg.norm(values_x) * np.linalg.norm(values_y))
    if denominator < 1e-12:
        return 0.0
    return float(np.dot(values_x, values_y) / denominator)


def _rank_average(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=np.float64)
    index = 0
    while index < values.size:
        stop = index + 1
        while stop < values.size and values[order[stop]] == values[order[index]]:
            stop += 1
        rank = (index + stop - 1) / 2.0
        ranks[order[index:stop]] = rank
        index = stop
    return ranks


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    mask = _finite_mask(x, y)
    if int(mask.sum()) < 2:
        return 0.0
    return _pearson(_rank_average(np.asarray(x[mask])), _rank_average(np.asarray(y[mask])))


def compute_ic(factor: np.ndarray, target: np.ndarray) -> float:
    """Mean per-symbol Pearson information coefficient."""

    factors = np.asarray(factor, dtype=np.float64)
    targets = np.asarray(target, dtype=np.float64)
    if factors.shape != targets.shape:
        raise ValueError("factor and target must share the same shape")
    if factors.ndim != 2:
        raise ValueError("factor and target must have shape [N, T]")
    values = [_pearson(factors[index], targets[index]) for index in range(factors.shape[0])]
    return float(np.mean(values, dtype=np.float64)) if values else 0.0


def compute_rank_ic(factor: np.ndarray, target: np.ndarray) -> float:
    """Mean per-symbol Spearman rank information coefficient."""

    factors = np.asarray(factor, dtype=np.float64)
    targets = np.asarray(target, dtype=np.float64)
    if factors.shape != targets.shape:
        raise ValueError("factor and target must share the same shape")
    if factors.ndim != 2:
        raise ValueError("factor and target must have shape [N, T]")
    values = [_spearman(factors[index], targets[index]) for index in range(factors.shape[0])]
    return float(np.mean(values, dtype=np.float64)) if values else 0.0


def forward_log_returns(frame: BarFrame, horizon: int = 1) -> np.ndarray:
    """Build causal labels where ``target[t]`` is the return from t to t+h."""

    if horizon < 1:
        raise ValueError("horizon must be positive")
    close = np.asarray(frame.close, dtype=np.float64)
    result = np.full_like(close, np.nan, dtype=np.float64)
    if close.shape[-1] > horizon:
        result[:, :-horizon] = np.log(close[:, horizon:] / close[:, :-horizon])
    return result


def _max_drawdown(returns: np.ndarray) -> float:
    finite = np.asarray(returns, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + np.clip(finite, -0.99, 10.0))
    peaks = np.maximum.accumulate(equity)
    drawdowns = 1.0 - equity / np.maximum(peaks, 1e-12)
    return float(np.max(drawdowns))


def max_abs_correlation(
    factor: np.ndarray,
    references: Sequence[np.ndarray],
) -> float:
    candidate = np.asarray(factor, dtype=np.float64).reshape(-1)
    maximum = 0.0
    for reference in references:
        values = np.asarray(reference, dtype=np.float64).reshape(-1)
        size = min(candidate.size, values.size)
        if size < 2:
            continue
        maximum = max(maximum, abs(_pearson(candidate[-size:], values[-size:])))
    return float(maximum)


def repetition_penalty(tokens: Sequence[int]) -> float:
    values = tuple(int(token) for token in tokens)
    if len(values) < 2:
        return 0.0
    bigrams = tuple(pairwise(values))
    adjacent = sum(left == right for left, right in bigrams)
    repeated_bigrams = len(bigrams) - len(set(bigrams))
    unique_ratio = 1.0 - len(set(values)) / len(values)
    return float(
        min(
            1.0,
            0.5 * adjacent / (len(values) - 1)
            + 0.3 * repeated_bigrams / max(1, len(values) - 1)
            + 0.2 * unique_ratio,
        )
    )


@dataclass(frozen=True)
class RewardConfig:
    horizon: int = 1
    ic_weight: float = 0.45
    rank_ic_weight: float = 0.35
    return_weight: float = 0.20
    volatility_weight: float = 0.05
    drawdown_weight: float = 0.10
    turnover_weight: float = 0.05
    transaction_cost_bps: float = 5.0
    cost_weight: float = 1.0
    correlation_threshold: float = 0.85
    correlation_weight: float = 0.25
    repetition_weight: float = 0.10
    annualization: float = 252.0

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("horizon must be positive")
        if self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps cannot be negative")
        if not 0.0 <= self.correlation_threshold <= 1.0:
            raise ValueError("correlation_threshold must be in [0, 1]")


@dataclass(frozen=True)
class RewardResult:
    train_reward: float
    validation_score: float
    score: float
    ic: float
    rank_ic: float
    return_proxy: float
    volatility: float
    max_drawdown: float
    turnover: float
    cost_penalty: float
    correlation_penalty: float
    repetition_penalty: float
    exposure: float
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def sharpe(self) -> float:
        return float(
            self.return_proxy
            / (self.volatility + 1e-12)
            * np.sqrt(max(1.0, float(self.metrics.get("annualization", 252.0))))
        )

    @property
    def sortino(self) -> float:
        # The downside estimator is folded into volatility for this compact proxy.
        return self.sharpe

    @property
    def drawdown_proxy(self) -> float:
        return self.max_drawdown

    @property
    def gates(self) -> Mapping[str, bool]:
        return {
            "ic_positive": self.ic > 0.0,
            "rank_ic_positive": self.rank_ic > 0.0,
            "has_exposure": self.exposure > 0.0,
        }

    @property
    def diagnostics(self) -> Mapping[str, Any]:
        return self.metrics

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metrics"] = dict(self.metrics)
        payload["sharpe"] = self.sharpe
        payload["sortino"] = self.sortino
        payload["drawdown_proxy"] = self.drawdown_proxy
        payload["gates"] = dict(self.gates)
        payload["diagnostics"] = dict(self.diagnostics)
        return payload


class RewardScorer:
    """Risk-aware scoring over SignalKernel positions and causal labels."""

    def __init__(
        self,
        config: RewardConfig | None = None,
        signal_kernel: SignalKernel | None = None,
    ) -> None:
        self.config = config or RewardConfig()
        self.signal_kernel = signal_kernel or SignalKernel()

    def repetition_penalty(self, tokens: Sequence[int]) -> float:
        return repetition_penalty(tokens)

    def correlation_penalty(
        self,
        factor: np.ndarray,
        references: Sequence[np.ndarray],
    ) -> tuple[float, float]:
        correlation = max_abs_correlation(factor, references)
        excess = max(0.0, correlation - self.config.correlation_threshold)
        denominator = max(1e-12, 1.0 - self.config.correlation_threshold)
        return correlation, self.config.correlation_weight * excess / denominator

    def evaluate(
        self,
        factor: np.ndarray,
        target: np.ndarray,
        *,
        positions: np.ndarray | None = None,
        formula_tokens: Sequence[int] = (),
        reference_factors: Sequence[np.ndarray] = (),
        validation_score: float | None = None,
    ) -> RewardResult:
        factors = np.asarray(factor, dtype=np.float64)
        targets = np.asarray(target, dtype=np.float64)
        if factors.ndim != 2 or targets.ndim != 2 or factors.shape != targets.shape:
            raise ValueError("factor and target must share shape [N, T]")

        if positions is None:
            position_values = self.signal_kernel.positions(factors)
        else:
            position_values = np.asarray(positions, dtype=np.float64)
            if position_values.shape != factors.shape:
                raise ValueError("positions must share the factor shape")

        valid = np.isfinite(position_values) & np.isfinite(targets)
        pnl = np.where(valid, position_values * targets, np.nan)
        per_symbol_return = [
            float(np.nanmean(pnl[index])) if np.isfinite(pnl[index]).any() else 0.0
            for index in range(pnl.shape[0])
        ]
        per_symbol_volatility = [
            float(np.nanstd(pnl[index])) if np.isfinite(pnl[index]).any() else 0.0
            for index in range(pnl.shape[0])
        ]
        per_symbol_drawdown = [_max_drawdown(pnl[index]) for index in range(pnl.shape[0])]
        per_symbol_turnover = []
        for index in range(position_values.shape[0]):
            previous = np.concatenate([np.zeros(1, dtype=np.float64), position_values[index, :-1]])
            per_symbol_turnover.append(float(np.mean(np.abs(position_values[index] - previous))))

        return_proxy = float(np.mean(per_symbol_return, dtype=np.float64))
        volatility = float(np.mean(per_symbol_volatility, dtype=np.float64))
        drawdown = float(np.mean(per_symbol_drawdown, dtype=np.float64))
        turnover = float(np.mean(per_symbol_turnover, dtype=np.float64))
        cost_penalty = (
            turnover * self.config.transaction_cost_bps / 10_000.0 * self.config.cost_weight
        )
        correlation, corr_penalty = self.correlation_penalty(factors, reference_factors)
        repeat_penalty = self.repetition_penalty(formula_tokens) * self.config.repetition_weight

        annualized_return = np.clip(
            return_proxy * np.sqrt(max(1.0, self.config.annualization)),
            -10.0,
            10.0,
        )
        base_score = (
            self.config.ic_weight * compute_ic(factors, targets)
            + self.config.rank_ic_weight * compute_rank_ic(factors, targets)
            + self.config.return_weight * float(annualized_return)
            - self.config.volatility_weight * volatility
            - self.config.drawdown_weight * drawdown
            - self.config.turnover_weight * turnover
        )
        score = float(base_score - cost_penalty - corr_penalty - repeat_penalty)
        metrics = {
            "correlation": correlation,
            "annualized_return_proxy": float(annualized_return),
            "annualization": self.config.annualization,
            "formula_length": len(tuple(formula_tokens)),
            "horizon": self.config.horizon,
        }
        return RewardResult(
            train_reward=score,
            validation_score=score if validation_score is None else float(validation_score),
            score=score,
            ic=compute_ic(factors, targets),
            rank_ic=compute_rank_ic(factors, targets),
            return_proxy=return_proxy,
            volatility=volatility,
            max_drawdown=drawdown,
            turnover=turnover,
            cost_penalty=cost_penalty,
            correlation_penalty=corr_penalty,
            repetition_penalty=repeat_penalty,
            exposure=float(np.mean(np.abs(position_values), dtype=np.float64)),
            metrics=metrics,
        )

    def score(
        self,
        factor: np.ndarray,
        target: np.ndarray,
        **kwargs: Any,
    ) -> RewardResult:
        return self.evaluate(factor, target, **kwargs)


MiningReward = RewardScorer
Scorer = RewardScorer


__all__ = [
    "MiningReward",
    "RewardConfig",
    "RewardResult",
    "RewardScorer",
    "Scorer",
    "compute_ic",
    "compute_rank_ic",
    "forward_log_returns",
    "max_abs_correlation",
    "repetition_penalty",
]

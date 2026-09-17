from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .reward import max_abs_correlation


@dataclass(frozen=True)
class FactorCandidate:
    formula_tokens: tuple[int, ...]
    score: float
    factor: np.ndarray
    step: int = 0
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "formula_tokens",
            tuple(int(token) for token in self.formula_tokens),
        )
        factor = np.asarray(self.factor, dtype=np.float64)
        if factor.ndim != 2 or not np.isfinite(factor).all():
            raise ValueError("factor candidate must be a finite [N, T] matrix")
        object.__setattr__(self, "factor", factor.copy())

    def state_dict(self) -> dict[str, Any]:
        return {
            "formula_tokens": self.formula_tokens,
            "score": self.score,
            "factor": self.factor,
            "step": self.step,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_state_dict(cls, state: Mapping[str, Any]) -> FactorCandidate:
        return cls(
            formula_tokens=tuple(state["formula_tokens"]),
            score=float(state["score"]),
            factor=np.asarray(state["factor"], dtype=np.float64),
            step=int(state.get("step", 0)),
            metrics=dict(state.get("metrics", {})),
        )


class FactorPool:
    """Top-K factor tensor memory used for diversity penalties."""

    def __init__(self, capacity: int = 25) -> None:
        if capacity < 1:
            raise ValueError("factor pool capacity must be positive")
        self.capacity = int(capacity)
        self._candidates: dict[tuple[int, ...], FactorCandidate] = {}

    def __len__(self) -> int:
        return len(self._candidates)

    def __iter__(self) -> Iterator[FactorCandidate]:
        return iter(self.candidates)

    @property
    def candidates(self) -> tuple[FactorCandidate, ...]:
        return tuple(
            sorted(
                self._candidates.values(),
                key=lambda candidate: (
                    -candidate.score,
                    candidate.step,
                    candidate.formula_tokens,
                ),
            )
        )

    def add(
        self,
        score: float,
        formula_tokens: Sequence[int],
        factor: np.ndarray,
        step: int = 0,
        *,
        metrics: Mapping[str, Any] | None = None,
    ) -> bool:
        candidate = FactorCandidate(
            formula_tokens=tuple(formula_tokens),
            score=float(score),
            factor=np.asarray(factor, dtype=np.float64),
            step=int(step),
            metrics=dict(metrics or {}),
        )
        previous = self._candidates.get(candidate.formula_tokens)
        if previous is not None and previous.score >= candidate.score:
            return False
        self._candidates[candidate.formula_tokens] = candidate
        ordered = self.candidates
        if len(ordered) > self.capacity:
            for removed in ordered[self.capacity :]:
                self._candidates.pop(removed.formula_tokens, None)
        return candidate in self.candidates

    def update(
        self,
        score: float,
        formula_tokens: Sequence[int],
        factor: np.ndarray,
        step: int = 0,
        *,
        metrics: Mapping[str, Any] | None = None,
    ) -> bool:
        return self.add(
            score,
            formula_tokens,
            factor,
            step,
            metrics=metrics,
        )

    def references(self, *, limit: int | None = None) -> tuple[np.ndarray, ...]:
        candidates = self.candidates
        if limit is not None:
            candidates = candidates[: max(0, int(limit))]
        return tuple(candidate.factor for candidate in candidates)

    def max_correlation(self, factor: np.ndarray) -> float:
        return max_abs_correlation(factor, self.references())

    def penalty(
        self,
        factor: np.ndarray,
        *,
        threshold: float = 0.85,
        weight: float = 1.0,
    ) -> float:
        correlation = self.max_correlation(factor)
        excess = max(0.0, correlation - threshold)
        return weight * excess / max(1e-12, 1.0 - threshold)

    def clear(self) -> None:
        self._candidates.clear()

    def state_dict(self) -> dict[str, Any]:
        return {
            "capacity": self.capacity,
            "candidates": [candidate.state_dict() for candidate in self.candidates],
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.capacity = int(state.get("capacity", self.capacity))
        self._candidates.clear()
        for item in state.get("candidates", ()):
            candidate = FactorCandidate.from_state_dict(item)
            self._candidates[candidate.formula_tokens] = candidate
        for removed in self.candidates[self.capacity :]:
            self._candidates.pop(removed.formula_tokens, None)


__all__ = ["FactorCandidate", "FactorPool"]

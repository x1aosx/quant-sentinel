from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class EliteCandidate:
    formula_tokens: tuple[int, ...]
    score: float
    step: int = 0
    birth_step: int | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "formula_tokens",
            tuple(int(token) for token in self.formula_tokens),
        )
        if not np.isfinite(self.score):
            raise ValueError("elite candidate score must be finite")
        if self.step < 0:
            raise ValueError("elite candidate step cannot be negative")
        if self.birth_step is not None and self.birth_step < 0:
            raise ValueError("elite candidate birth_step cannot be negative")

    @property
    def tokens(self) -> tuple[int, ...]:
        return self.formula_tokens

    @property
    def age(self) -> int:
        birth = self.step if self.birth_step is None else self.birth_step
        return max(0, self.step - birth)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["formula_tokens"] = list(self.formula_tokens)
        payload["metrics"] = dict(self.metrics)
        return payload


class ElitePool:
    """Top-K formula memory with exact-token deduplication and age decay."""

    def __init__(
        self,
        capacity: int = 20,
        *,
        decay_half_life: int = 300,
        seed: int | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("elite pool capacity must be positive")
        if decay_half_life < 1:
            raise ValueError("decay_half_life must be positive")
        self.capacity = int(capacity)
        self.decay_half_life = int(decay_half_life)
        self.rng = np.random.default_rng(seed)
        self._candidates: dict[tuple[int, ...], EliteCandidate] = {}

    def __len__(self) -> int:
        return len(self._candidates)

    def __iter__(self) -> Iterator[EliteCandidate]:
        return iter(self.candidates)

    @property
    def candidates(self) -> tuple[EliteCandidate, ...]:
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

    @property
    def best(self) -> EliteCandidate | None:
        candidates = self.candidates
        return candidates[0] if candidates else None

    def add(
        self,
        score: float,
        formula_tokens: Sequence[int],
        step: int = 0,
        *,
        metrics: Mapping[str, Any] | None = None,
    ) -> bool:
        tokens = tuple(int(token) for token in formula_tokens)
        candidate = EliteCandidate(
            formula_tokens=tokens,
            score=float(score),
            step=int(step),
            birth_step=int(step),
            metrics=dict(metrics or {}),
        )
        previous = self._candidates.get(tokens)
        if previous is not None and previous.score >= candidate.score:
            return False
        self._candidates[tokens] = candidate
        ordered = self.candidates
        if len(ordered) > self.capacity:
            for removed in ordered[self.capacity :]:
                self._candidates.pop(removed.formula_tokens, None)
            return candidate in self.candidates
        return True

    def update(
        self,
        score: float,
        formula_tokens: Sequence[int],
        step: int = 0,
        *,
        metrics: Mapping[str, Any] | None = None,
    ) -> bool:
        return self.add(score, formula_tokens, step, metrics=metrics)

    def replay_weights(self, current_step: int) -> np.ndarray:
        candidates = self.candidates
        if not candidates:
            return np.zeros(0, dtype=np.float64)
        weights = np.asarray(
            [
                0.5
                ** (
                    max(0, int(current_step) - (candidate.birth_step or candidate.step))
                    / self.decay_half_life
                )
                for candidate in candidates
            ],
            dtype=np.float64,
        )
        scores = np.asarray([candidate.score for candidate in candidates], dtype=np.float64)
        if scores.size > 1 and float(scores.max()) > float(scores.min()):
            scores = (scores - float(scores.min())) / (float(scores.max()) - float(scores.min()))
            weights *= np.exp(scores)
        total = float(weights.sum())
        return weights / total if total > 0 else np.full(len(candidates), 1.0 / len(candidates))

    def sample(
        self, current_step: int = 0, rng: np.random.Generator | None = None
    ) -> EliteCandidate:
        candidates = self.candidates
        if not candidates:
            raise ValueError("cannot sample from an empty elite pool")
        generator = self.rng if rng is None else rng
        index = int(generator.choice(len(candidates), p=self.replay_weights(current_step)))
        return candidates[index]

    def clear(self) -> None:
        self._candidates.clear()

    def state_dict(self) -> dict[str, Any]:
        return {
            "capacity": self.capacity,
            "decay_half_life": self.decay_half_life,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.capacity = int(state.get("capacity", self.capacity))
        self.decay_half_life = int(state.get("decay_half_life", self.decay_half_life))
        self._candidates.clear()
        for item in state.get("candidates", ()):
            candidate = EliteCandidate(
                formula_tokens=tuple(item["formula_tokens"]),
                score=float(item["score"]),
                step=int(item.get("step", 0)),
                birth_step=item.get("birth_step"),
                metrics=dict(item.get("metrics", {})),
            )
            self._candidates[candidate.formula_tokens] = candidate
        for removed in self.candidates[self.capacity :]:
            self._candidates.pop(removed.formula_tokens, None)


__all__ = ["EliteCandidate", "ElitePool"]

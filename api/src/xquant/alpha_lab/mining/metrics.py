from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TrainingMetrics:
    step: int
    reward: float
    validation_score: float
    ic: float
    rank_ic: float
    entropy: float
    best_score: float
    elite_pool_size: int
    invalid_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TrainingMetrics:
        return cls(
            step=int(payload["step"]),
            reward=float(payload["reward"]),
            validation_score=float(payload["validation_score"]),
            ic=float(payload["ic"]),
            rank_ic=float(payload["rank_ic"]),
            entropy=float(payload["entropy"]),
            best_score=float(payload["best_score"]),
            elite_pool_size=int(payload["elite_pool_size"]),
            invalid_rate=float(payload.get("invalid_rate", 0.0)),
        )


__all__ = ["TrainingMetrics"]

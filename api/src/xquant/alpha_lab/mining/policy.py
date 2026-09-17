from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .sampler import ConstrainedFormulaSampler, FormulaSample


@dataclass(frozen=True)
class PolicyUpdate:
    policy_loss: float
    mean_entropy: float
    entropy_coefficient: float
    advantage_mean: float
    advantage_std: float
    gradient_norm: float


class FormulaPolicy(Protocol):
    def next_token_logits(
        self,
        prefix_tokens: tuple[int, ...],
        context: Any = None,
    ) -> np.ndarray: ...


class NumpyPolicy:
    """Positional categorical policy with a dependency-free REINFORCE update."""

    def __init__(
        self,
        vocab_size: int,
        max_length: int,
        *,
        learning_rate: float = 0.02,
        entropy_weight: float = 0.01,
        entropy_floor: float = 0.5,
        entropy_floor_weight: float = 1.0,
        gradient_clip: float = 5.0,
        seed: int | None = None,
    ) -> None:
        if vocab_size < 1 or max_length < 1:
            raise ValueError("policy dimensions must be positive")
        self.vocab_size = int(vocab_size)
        self.max_length = int(max_length)
        self.learning_rate = float(learning_rate)
        self.entropy_weight = float(entropy_weight)
        self.entropy_floor = float(entropy_floor)
        self.entropy_floor_weight = float(entropy_floor_weight)
        self.gradient_clip = float(gradient_clip)
        self.weights = np.zeros((self.max_length, self.vocab_size), dtype=np.float64)
        self.update_step = 0
        self.rng = np.random.default_rng(seed)

    @property
    def device(self) -> str:
        return "numpy"

    def to(self, device: str | None = None) -> NumpyPolicy:
        del device
        return self

    def logits(self, position: int) -> np.ndarray:
        if position < 0 or position >= self.max_length:
            raise IndexError("policy position is outside max_length")
        return self.weights[position].copy()

    def next_token_logits(
        self,
        prefix_tokens: tuple[int, ...],
        context: Any = None,
    ) -> np.ndarray:
        del context
        return self.logits(len(prefix_tokens))

    @staticmethod
    def probabilities(
        logits: np.ndarray,
        mask: np.ndarray | None = None,
        temperature: float = 1.0,
    ) -> np.ndarray:
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        scores = np.asarray(logits, dtype=np.float64) / float(temperature)
        if mask is None:
            active = np.ones(scores.shape, dtype=bool)
        else:
            active = np.asarray(mask, dtype=bool)
            if active.shape != scores.shape:
                raise ValueError("mask shape must match logits")
        if not active.any():
            raise ValueError("policy mask must allow at least one token")
        masked_scores = np.where(active, scores, -np.inf)
        masked_scores -= np.max(masked_scores)
        probabilities = np.exp(masked_scores)
        total = float(probabilities.sum())
        if not np.isfinite(total) or total <= 0:
            probabilities = active.astype(np.float64)
            total = float(probabilities.sum())
        return probabilities / total

    def entropy_coefficient(self, mean_entropy: float) -> float:
        base = self.entropy_weight
        if mean_entropy < self.entropy_floor:
            gap = self.entropy_floor - mean_entropy
            base += self.entropy_floor_weight * gap
        return float(base)

    def sample_many(
        self,
        sampler: ConstrainedFormulaSampler,
        rng: np.random.Generator | None,
        count: int,
        *,
        temperature: float = 1.0,
    ) -> tuple[FormulaSample, ...]:
        if count < 0:
            raise ValueError("count cannot be negative")
        generator = self.rng if rng is None else rng
        return tuple(
            sampler.sample_detailed(
                rng=generator,
                logits=lambda _position, prefix: self.next_token_logits(prefix),
                temperature=temperature,
            )
            for _ in range(int(count))
        )

    @staticmethod
    def _entropy_gradient(probabilities: np.ndarray) -> np.ndarray:
        entropy = float(-np.sum(probabilities * np.log(probabilities + 1e-12)))
        return -probabilities * (np.log(probabilities + 1e-12) + entropy)

    def update(
        self,
        samples: tuple[FormulaSample, ...] | list[FormulaSample],
        rewards: np.ndarray,
    ) -> PolicyUpdate:
        values = np.asarray(rewards, dtype=np.float64).reshape(-1)
        if values.size != len(samples):
            raise ValueError("rewards and samples must have the same length")
        if values.size == 0:
            return PolicyUpdate(0.0, 0.0, self.entropy_weight, 0.0, 0.0, 0.0)

        reward_std = float(values.std())
        if reward_std < 1e-8:
            advantages = np.zeros_like(values)
        else:
            advantages = (values - float(values.mean())) / (reward_std + 1e-8)

        mean_entropy = float(np.mean([sample.entropy for sample in samples], dtype=np.float64))
        entropy_coefficient = self.entropy_coefficient(mean_entropy)
        gradient = np.zeros_like(self.weights)
        policy_loss = 0.0

        for sample, advantage in zip(samples, advantages, strict=True):
            for position, token in enumerate(sample.tokens):
                mask = sample.step_masks[position]
                probabilities = self.probabilities(self.weights[position], mask)
                score_gradient = -probabilities
                score_gradient[token] += 1.0
                entropy_gradient = self._entropy_gradient(probabilities)
                gradient[position] += (
                    float(advantage) * score_gradient + entropy_coefficient * entropy_gradient
                )
                policy_loss -= float(advantage) * sample.step_log_probs[position]

        policy_loss /= values.size
        gradient /= values.size
        gradient_norm = float(np.linalg.norm(gradient))
        if self.gradient_clip > 0 and gradient_norm > self.gradient_clip:
            gradient *= self.gradient_clip / gradient_norm
            gradient_norm = self.gradient_clip
        self.weights += self.learning_rate * gradient
        self.update_step += 1
        return PolicyUpdate(
            policy_loss=float(policy_loss),
            mean_entropy=mean_entropy,
            entropy_coefficient=entropy_coefficient,
            advantage_mean=float(advantages.mean()),
            advantage_std=float(advantages.std()),
            gradient_norm=gradient_norm,
        )

    def state_dict(self) -> dict[str, Any]:
        return {
            "weights": self.weights.copy(),
            "vocab_size": self.vocab_size,
            "max_length": self.max_length,
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        weights = np.asarray(state["weights"], dtype=np.float64)
        if weights.shape != self.weights.shape:
            raise ValueError("policy weights have an incompatible shape")
        self.weights = weights.copy()

    def optimizer_state_dict(self) -> dict[str, Any]:
        return {
            "update_step": self.update_step,
            "learning_rate": self.learning_rate,
        }

    def load_optimizer_state_dict(self, state: dict[str, Any]) -> None:
        self.update_step = int(state.get("update_step", 0))
        self.learning_rate = float(state.get("learning_rate", self.learning_rate))


__all__ = ["FormulaPolicy", "NumpyPolicy", "PolicyUpdate"]

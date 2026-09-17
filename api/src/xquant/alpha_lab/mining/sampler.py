from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from ..config import MiningSettings
from ..factor import FEATURE_REGISTRY, FORMULA_VOCAB, OPERATOR_REGISTRY

LogitsProvider = Callable[[int, tuple[int, ...]], Sequence[float] | np.ndarray]


@dataclass(frozen=True)
class FormulaSample:
    """A sampled formula and the policy statistics needed for REINFORCE."""

    tokens: tuple[int, ...]
    log_prob: float
    entropy: float
    step_log_probs: tuple[float, ...] = ()
    step_entropies: tuple[float, ...] = ()
    step_masks: tuple[np.ndarray, ...] = ()


class ConstrainedFormulaSampler:
    """Sample stack-valid formulas directly from the active factor vocabulary."""

    def __init__(
        self,
        min_length: int | None = None,
        max_length: int | None = None,
        seed: int | None = None,
        *,
        settings: MiningSettings | None = None,
        vocab_size: int | None = None,
        feat_offset: int | None = None,
        arity_map: dict[int, int] | None = None,
    ) -> None:
        active_settings = settings or MiningSettings()
        self.min_length = (
            active_settings.min_formula_length if min_length is None else int(min_length)
        )
        self.max_length = (
            active_settings.max_formula_length if max_length is None else int(max_length)
        )
        if self.min_length < 1 or self.max_length < self.min_length:
            raise ValueError("formula length constraints are invalid")

        self.feature_count = len(FEATURE_REGISTRY.names)
        self.operator_offset = self.feature_count
        self.vocab_size = len(FORMULA_VOCAB.token_names)
        self.feat_offset = self.operator_offset
        if vocab_size is not None and int(vocab_size) != self.vocab_size:
            raise ValueError("vocab_size does not match the active FORMULA_VOCAB")
        if feat_offset is not None and int(feat_offset) != self.feat_offset:
            raise ValueError("feat_offset does not match the active feature registry")

        registry_arity = {
            self.operator_offset + index: spec.arity
            for index, spec in enumerate(OPERATOR_REGISTRY.specs)
        }
        if arity_map is not None:
            normalized = {int(token): int(arity) for token, arity in arity_map.items()}
            if any(
                registry_arity.get(token) != arity
                for token, arity in normalized.items()
                if token >= self.operator_offset
            ):
                raise ValueError("arity_map does not match OPERATOR_REGISTRY")
            registry_arity = normalized
        self.arity_map = registry_arity
        self.delta = {
            token: (1 if token < self.operator_offset else 1 - self.arity_map[token])
            for token in range(self.vocab_size)
        }
        self.rng = np.random.default_rng(seed)
        self._finish_cache: dict[tuple[int, int], bool] = {}
        self._mask_cache: dict[tuple[int, int, int], np.ndarray] = {}

    def _can_finish(self, depth: int, remaining: int) -> bool:
        key = (depth, remaining)
        if key in self._finish_cache:
            return self._finish_cache[key]
        if remaining == 0:
            self._finish_cache[key] = depth == 1
            return self._finish_cache[key]
        if depth < 0:
            self._finish_cache[key] = False
            return False
        for delta in set(self.delta.values()):
            next_depth = depth + delta
            if next_depth >= 1 and self._can_finish(next_depth, remaining - 1):
                self._finish_cache[key] = True
                return True
        self._finish_cache[key] = False
        return False

    def length_is_valid(self, length: int) -> bool:
        return self.min_length <= int(length) <= self.max_length and self._can_finish(
            0, int(length)
        )

    @property
    def valid_lengths(self) -> tuple[int, ...]:
        return tuple(
            length
            for length in range(self.min_length, self.max_length + 1)
            if self.length_is_valid(length)
        )

    def can_apply(self, token: int, stack_depth: int) -> bool:
        if token < self.operator_offset:
            return True
        arity = self.arity_map.get(int(token))
        return arity is not None and stack_depth >= arity

    def valid_token_mask(
        self,
        stack_depth: int,
        step_index: int,
        total_length: int,
    ) -> np.ndarray:
        if step_index < 0 or step_index >= total_length:
            raise ValueError("step_index must be inside the formula")
        cache_key = (stack_depth, step_index, total_length)
        cached = self._mask_cache.get(cache_key)
        if cached is not None:
            return cached.copy()
        remaining = total_length - step_index - 1
        mask = np.zeros(self.vocab_size, dtype=bool)
        for token, delta in self.delta.items():
            if not self.can_apply(token, stack_depth):
                continue
            next_depth = stack_depth + delta
            if next_depth >= 1 and self._can_finish(next_depth, remaining):
                mask[token] = True
        self._mask_cache[cache_key] = mask
        return mask.copy()

    def valid_mask(
        self,
        stack_depth: int,
        step_idx: int,
        total_steps: int,
        device: object | None = None,
        prev_token: int | None = None,
        infected_chain_len: int = 0,
    ) -> np.ndarray:
        # Compatibility shim for the upstream sampler call shape.
        del device, prev_token, infected_chain_len
        return self.valid_token_mask(stack_depth, step_idx, total_steps)

    def apply_mask_to_logits(
        self,
        logits: np.ndarray,
        stack_depths: Sequence[int],
        step_idx: int,
        total_steps: int,
    ) -> np.ndarray:
        values = np.asarray(logits, dtype=np.float64).copy()
        if values.ndim == 1:
            values = values[None, :]
        if values.shape[1] != self.vocab_size:
            raise ValueError("logits have the wrong vocabulary dimension")
        for row, depth in enumerate(stack_depths):
            mask = self.valid_token_mask(int(depth), step_idx, total_steps)
            values[row, ~mask] = -np.inf
        return values

    def is_valid(self, tokens: Sequence[int]) -> bool:
        values = tuple(int(token) for token in tokens)
        if not self.length_is_valid(len(values)):
            return False
        depth = 0
        for token in values:
            if token < 0 or token >= self.vocab_size or not self.can_apply(token, depth):
                return False
            depth += self.delta[token]
            if depth < 1:
                return False
        return depth == 1

    def validate(self, tokens: Sequence[int]) -> tuple[int, ...]:
        values = tuple(int(token) for token in tokens)
        if not self.is_valid(values):
            raise ValueError("formula violates arity, stack depth, or length constraints")
        return values

    @staticmethod
    def _probabilities(logits: np.ndarray, mask: np.ndarray, temperature: float) -> np.ndarray:
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        scores = np.asarray(logits, dtype=np.float64) / float(temperature)
        scores = np.where(mask, scores, -np.inf)
        scores -= np.max(scores)
        probabilities = np.exp(scores)
        total = float(probabilities.sum())
        if not np.isfinite(total) or total <= 0:
            probabilities = mask.astype(np.float64)
            total = float(probabilities.sum())
        return probabilities / total

    def sample_detailed(
        self,
        rng: np.random.Generator | None = None,
        *,
        length: int | None = None,
        logits: LogitsProvider | None = None,
        temperature: float = 1.0,
    ) -> FormulaSample:
        generator = self.rng if rng is None else rng
        if length is None:
            lengths = self.valid_lengths
            if not lengths:
                raise ValueError("no feasible formula length satisfies the constraints")
            total_length = int(generator.choice(lengths))
        else:
            total_length = int(length)
        if not self.length_is_valid(total_length):
            raise ValueError("requested formula length is not feasible")

        tokens: list[int] = []
        log_probs: list[float] = []
        entropies: list[float] = []
        masks: list[np.ndarray] = []
        depth = 0
        for step_index in range(total_length):
            mask = self.valid_token_mask(depth, step_index, total_length)
            if not mask.any():
                raise RuntimeError("formula sampler reached an empty valid-token mask")
            scores = (
                np.asarray(logits(step_index, tuple(tokens)), dtype=np.float64)
                if logits is not None
                else np.zeros(self.vocab_size, dtype=np.float64)
            )
            if scores.shape != (self.vocab_size,):
                raise ValueError("logits provider returned the wrong shape")
            probabilities = self._probabilities(scores, mask, temperature)
            token = int(generator.choice(self.vocab_size, p=probabilities))
            probability = max(float(probabilities[token]), np.finfo(np.float64).tiny)
            tokens.append(token)
            log_probs.append(float(np.log(probability)))
            entropies.append(float(-np.sum(probabilities * np.log(probabilities + 1e-12))))
            masks.append(mask)
            depth += self.delta[token]

        if depth != 1:
            raise RuntimeError("formula sampler produced an invalid residual stack")
        return FormulaSample(
            tokens=tuple(tokens),
            log_prob=float(np.sum(log_probs)),
            entropy=float(np.mean(entropies)) if entropies else 0.0,
            step_log_probs=tuple(log_probs),
            step_entropies=tuple(entropies),
            step_masks=tuple(masks),
        )

    def sample(
        self,
        rng: np.random.Generator | None = None,
        *,
        length: int | None = None,
    ) -> tuple[int, ...]:
        return self.sample_detailed(rng=rng, length=length).tokens

    def sample_many(
        self,
        count: int,
        rng: np.random.Generator | None = None,
    ) -> tuple[tuple[int, ...], ...]:
        if count < 0:
            raise ValueError("count cannot be negative")
        generator = self.rng if rng is None else rng
        return tuple(self.sample(generator) for _ in range(int(count)))

    def invalid_rate(self, formulas: Sequence[Sequence[int]]) -> float:
        values = tuple(formulas)
        if not values:
            return 0.0
        return sum(not self.is_valid(formula) for formula in values) / len(values)


ConstrainedSampler = ConstrainedFormulaSampler


__all__ = [
    "ConstrainedFormulaSampler",
    "ConstrainedSampler",
    "FormulaSample",
    "LogitsProvider",
]

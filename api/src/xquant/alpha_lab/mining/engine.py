from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from ..config import MiningSettings
from ..data import BarFrame
from ..domain import TrainingRun, TrainingStatus
from ..errors import AlphaLabError
from ..factor import (
    FEATURE_REGISTRY,
    FORMULA_VOCAB,
    FactorRuntime,
    FeatureRegistry,
    SignalKernel,
)
from .checkpoint import TrainingCheckpoint, restore_numpy_generator
from .elite_pool import EliteCandidate, ElitePool
from .factor_pool import FactorPool
from .metrics import TrainingMetrics
from .policy import NumpyPolicy, PolicyUpdate
from .reward import (
    MiningReward,
    RewardConfig,
    RewardResult,
    forward_log_returns,
)
from .sampler import ConstrainedFormulaSampler, FormulaSample
from .walk_forward import WalkForwardFold, WalkForwardPlan


@dataclass(frozen=True)
class TrainingResult:
    run: TrainingRun
    metrics: TrainingMetrics
    history: tuple[TrainingMetrics, ...]
    elite_candidates: tuple[EliteCandidate, ...]

    @property
    def steps(self) -> int:
        return self.run.step

    @property
    def best_formula_tokens(self) -> tuple[int, ...]:
        return self.run.best_formula_tokens

    @property
    def best_score(self) -> float | None:
        return self.run.best_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "metrics": self.metrics.to_dict(),
            "history": [metrics.to_dict() for metrics in self.history],
            "elite_candidates": [candidate.to_dict() for candidate in self.elite_candidates],
        }


class MiningEngine:
    """Dependency-light factor mining loop using REINFORCE with elite replay."""

    def __init__(
        self,
        frame: BarFrame,
        run: TrainingRun | None = None,
        *,
        settings: MiningSettings | None = None,
        reward_config: RewardConfig | None = None,
        plan: WalkForwardPlan | None = None,
        feature_registry: FeatureRegistry = FEATURE_REGISTRY,
        runtime: FactorRuntime | None = None,
        signal_kernel: SignalKernel | None = None,
        batch_size: int = 32,
        elite_pool_size: int = 20,
        factor_pool_size: int | None = None,
        seed: int | None = None,
        device: str | None = None,
    ) -> None:
        if batch_size < 1 or elite_pool_size < 1:
            raise ValueError("batch_size and elite_pool_size must be positive")
        self.frame = frame
        self.settings = settings or MiningSettings()
        self.seed = self.settings.seed if seed is None else int(seed)
        self.device = "numpy"
        del device
        self.batch_size = int(batch_size)
        self.feature_registry = feature_registry
        self.features = self.feature_registry.compute(frame)
        self.runtime = runtime or FactorRuntime()
        self.signal_kernel = signal_kernel or SignalKernel(self.settings)
        self.reward = MiningReward(reward_config, signal_kernel=self.signal_kernel)
        self.plan = plan or self._build_adaptive_plan(frame.n_bars)
        self.rng = np.random.default_rng(self.seed)
        self.sampler = ConstrainedFormulaSampler(
            settings=self.settings,
            seed=self.seed,
        )
        self.policy = NumpyPolicy(
            vocab_size=self.sampler.vocab_size,
            max_length=self.sampler.max_length,
            learning_rate=self.settings.learning_rate,
            entropy_weight=self.settings.entropy_weight,
            seed=self.seed,
        ).to(self.device)
        self.pool = ElitePool(
            capacity=elite_pool_size,
            seed=self.seed,
        )
        self.factor_pool = FactorPool(capacity=factor_pool_size or max(25, elite_pool_size))
        self.step = 0
        self.best_score: float | None = None
        self.best_formula_tokens: tuple[int, ...] = ()
        self.history: list[TrainingMetrics] = []
        self.run = run or TrainingRun(
            id=self._default_run_id(),
            dataset_id="",
            symbol=frame.symbol,
            timeframe=frame.timeframe,
        )

    def _default_run_id(self) -> str:
        digest = hashlib.sha256(
            f"{self.frame.symbol}:{self.frame.timeframe}:{self.seed}".encode()
        ).hexdigest()
        return digest[:20]

    def _build_adaptive_plan(self, n_bars: int) -> WalkForwardPlan:
        holdout = min(self.settings.holdout_bars, max(2, n_bars // 5))
        development = n_bars - holdout
        if development < 4:
            raise ValueError("bar frame is too short to create training folds")
        gap = min(self.settings.walk_forward_gap_bars, max(0, development // 50))
        requested_folds = min(self.settings.walk_forward_folds, max(1, development // 4))
        for folds in range(requested_folds, 0, -1):
            validation = min(
                self.settings.walk_forward_validation_bars,
                max(2, (development - gap - 2) // folds),
            )
            available_train = development - gap - validation * folds
            if available_train < 2:
                continue
            train = min(self.settings.walk_forward_train_bars, available_train)
            return WalkForwardPlan.build(
                n_bars,
                train_bars=train,
                validation_bars=validation,
                gap=gap,
                folds=folds,
                holdout_bars=holdout,
            )
        raise ValueError("bar frame is too short for walk-forward validation")

    def _factor(self, tokens: Sequence[int]) -> np.ndarray:
        return self.runtime.evaluate(
            tokens,
            self.features,
            FORMULA_VOCAB.schema_version,
        )

    def _score_on_fold(
        self,
        factor: np.ndarray,
        fold: WalkForwardFold,
        tokens: Sequence[int],
        reference_factors: Sequence[np.ndarray],
    ) -> RewardResult:
        train_frame = self.frame.slice_time(fold.train_start, fold.train_end)
        validation_frame = self.frame.slice_time(
            fold.validation_start,
            fold.validation_end,
        )
        train_target = forward_log_returns(train_frame, self.reward.config.horizon)
        validation_target = forward_log_returns(
            validation_frame,
            self.reward.config.horizon,
        )
        train_factor = factor[:, fold.train_start : fold.train_end]
        validation_factor = factor[:, fold.validation_start : fold.validation_end]
        train_result = self.reward.evaluate(
            train_factor,
            train_target,
            formula_tokens=tokens,
            reference_factors=reference_factors,
        )
        validation_result = self.reward.evaluate(
            validation_factor,
            validation_target,
            formula_tokens=tokens,
        )
        return replace(
            train_result,
            validation_score=validation_result.score,
            metrics={
                **dict(train_result.metrics),
                "validation_ic": validation_result.ic,
                "validation_rank_ic": validation_result.rank_ic,
            },
        )

    @staticmethod
    def _average_scores(results: Sequence[RewardResult]) -> RewardResult:
        if not results:
            raise ValueError("at least one reward result is required")

        def mean(name: str) -> float:
            return float(np.mean([float(getattr(result, name)) for result in results]))

        metrics: dict[str, Any] = {}
        for key in results[0].metrics:
            values = [result.metrics.get(key) for result in results]
            if values and all(isinstance(value, (int, float)) for value in values):
                metrics[key] = float(np.mean(values))
            else:
                metrics[key] = values[0]
        return RewardResult(
            train_reward=mean("train_reward"),
            validation_score=mean("validation_score"),
            score=mean("score"),
            ic=mean("ic"),
            rank_ic=mean("rank_ic"),
            return_proxy=mean("return_proxy"),
            volatility=mean("volatility"),
            max_drawdown=mean("max_drawdown"),
            turnover=mean("turnover"),
            cost_penalty=mean("cost_penalty"),
            correlation_penalty=mean("correlation_penalty"),
            repetition_penalty=mean("repetition_penalty"),
            exposure=mean("exposure"),
            metrics=metrics,
        )

    def evaluate_formula(
        self,
        tokens: Sequence[int],
        *,
        reference_factors: Sequence[np.ndarray] = (),
    ) -> RewardResult:
        factor = self._factor(tokens)
        results = [
            self._score_on_fold(
                factor,
                fold,
                tokens,
                reference_factors if index == 0 else (),
            )
            for index, fold in enumerate(self.plan.folds)
        ]
        return self._average_scores(results)

    def train_step(self) -> TrainingMetrics:
        samples: tuple[FormulaSample, ...] = self.policy.sample_many(
            self.sampler,
            self.rng,
            self.batch_size,
        )
        rewards = np.empty(len(samples), dtype=np.float64)
        validation_scores = np.empty(len(samples), dtype=np.float64)
        ic_values: list[float] = []
        rank_ic_values: list[float] = []
        invalid = 0

        for index, sample in enumerate(samples):
            try:
                if not self.sampler.is_valid(sample.tokens):
                    raise ValueError("sampler produced an invalid formula")
                first_fold = self.plan.folds[0]
                references = tuple(
                    reference[:, first_fold.train_start : first_fold.train_end]
                    for reference in self.factor_pool.references()
                )
                result = self.evaluate_formula(
                    sample.tokens,
                    reference_factors=references,
                )
                factor = self._factor(sample.tokens)
                rewards[index] = result.train_reward
                validation_scores[index] = result.validation_score
                ic_values.append(result.ic)
                rank_ic_values.append(result.rank_ic)
                self.factor_pool.add(
                    result.validation_score,
                    sample.tokens,
                    factor,
                    self.step,
                    metrics=result.to_dict(),
                )
                self.pool.add(
                    result.validation_score,
                    sample.tokens,
                    self.step,
                    metrics=result.to_dict(),
                )
                if self.best_score is None or result.validation_score > self.best_score:
                    self.best_score = result.validation_score
                    self.best_formula_tokens = sample.tokens
            except (AlphaLabError, ArithmeticError, IndexError, ValueError):
                invalid += 1
                rewards[index] = -5.0
                validation_scores[index] = -5.0

        update: PolicyUpdate = self.policy.update(samples, rewards)
        self.step += 1
        self.run = replace(
            self.run,
            status=TrainingStatus.RUNNING,
            step=self.step,
            progress=min(1.0, self.step / max(1, self.step)),
            best_formula_tokens=self.best_formula_tokens,
            best_score=self.best_score,
        )
        metrics = TrainingMetrics(
            step=self.step,
            reward=float(rewards.mean()),
            validation_score=float(validation_scores.mean()),
            ic=float(np.mean(ic_values)) if ic_values else 0.0,
            rank_ic=float(np.mean(rank_ic_values)) if rank_ic_values else 0.0,
            entropy=update.mean_entropy,
            best_score=(
                self.best_score if self.best_score is not None else float(validation_scores.max())
            ),
            elite_pool_size=len(self.pool),
            invalid_rate=invalid / max(1, len(samples)),
        )
        self.history.append(metrics)
        return metrics

    def train(self, steps: int = 1) -> TrainingResult:
        if steps < 0:
            raise ValueError("steps cannot be negative")
        for _ in range(int(steps)):
            self.train_step()
        if not self.history:
            raise ValueError("training did not produce any metrics")
        self.run = replace(
            self.run,
            status=TrainingStatus.SUCCEEDED,
            step=self.step,
            progress=1.0,
            best_formula_tokens=self.best_formula_tokens,
            best_score=self.best_score,
        )
        return TrainingResult(
            run=self.run,
            metrics=self.history[-1],
            history=tuple(self.history),
            elite_candidates=self.pool.candidates,
        )

    def checkpoint(self) -> TrainingCheckpoint:
        metrics = self.history[-1].to_dict() if self.history else {}
        return TrainingCheckpoint(
            step=self.step,
            model_state=self.policy.state_dict(),
            optimizer_state=self.policy.optimizer_state_dict(),
            elite_pool=self.pool.state_dict(),
            factor_pool=self.factor_pool.state_dict(),
            random_state={"numpy": self.rng.bit_generator.state},
            best_formula_tokens=self.best_formula_tokens,
            best_score=self.best_score,
            metrics=metrics,
            training_run=self.run.to_dict(),
            schema_version=FORMULA_VOCAB.schema_version,
            config_snapshot={
                "mining": asdict(self.settings),
                "reward": asdict(self.reward.config),
                "batch_size": self.batch_size,
            },
        )

    def resume(self, checkpoint: TrainingCheckpoint) -> MiningEngine:
        checkpoint.verify_schema(FORMULA_VOCAB.schema_version)
        self.policy.load_state_dict(dict(checkpoint.model_state))
        self.policy.load_optimizer_state_dict(dict(checkpoint.optimizer_state))
        self.pool.load_state_dict(dict(checkpoint.elite_pool))
        if checkpoint.factor_pool:
            self.factor_pool.load_state_dict(dict(checkpoint.factor_pool))
        restore_numpy_generator(self.rng, checkpoint.random_state)
        self.step = checkpoint.step
        self.best_score = checkpoint.best_score
        self.best_formula_tokens = tuple(checkpoint.best_formula_tokens)
        if checkpoint.training_run:
            try:
                status = TrainingStatus(str(checkpoint.training_run.get("status", "RUNNING")))
            except ValueError:
                status = TrainingStatus.RUNNING
            self.run = TrainingRun(
                id=str(checkpoint.training_run.get("id", self.run.id)),
                dataset_id=str(checkpoint.training_run.get("dataset_id", "")),
                symbol=str(checkpoint.training_run.get("symbol", self.frame.symbol)),
                timeframe=str(checkpoint.training_run.get("timeframe", self.frame.timeframe)),
                status=status,
                progress=float(checkpoint.training_run.get("progress", 0.0)),
                step=self.step,
                best_formula_tokens=self.best_formula_tokens,
                best_score=self.best_score,
                error=str(checkpoint.training_run.get("error", "")),
                checkpoint_uri=str(checkpoint.training_run.get("checkpoint_uri", "")),
            )
        return self

    def state_dict(self) -> dict[str, Any]:
        return self.checkpoint().to_payload()

    def load_state_dict(self, state: Mapping[str, Any]) -> MiningEngine:
        return self.resume(TrainingCheckpoint.from_payload(dict(state)))


AlphaEngine = MiningEngine
REINFORCETrainer = MiningEngine


__all__ = [
    "AlphaEngine",
    "MiningEngine",
    "REINFORCETrainer",
    "TrainingResult",
]

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np

from xquant.alpha_lab.config import MiningSettings
from xquant.alpha_lab.data import BarFrame
from xquant.alpha_lab.domain import TrainingRun
from xquant.alpha_lab.mining import (
    ConstrainedFormulaSampler,
    ElitePool,
    FactorPool,
    MiningEngine,
    RewardConfig,
    RewardScorer,
    TrainingCheckpoint,
    WalkForwardPlan,
    build_walk_forward_plan,
    compute_ic,
    compute_rank_ic,
    forward_log_returns,
    repetition_penalty,
)


def _frame(n_bars: int = 320) -> BarFrame:
    x = np.arange(n_bars, dtype=np.float64)
    close = 100.0 + np.sin(x / 9.0) * 3.0 + x * 0.04 + np.cos(x / 17.0)
    open_ = close + np.sin(x / 5.0) * 0.2
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = 1_000.0 + x + np.sin(x / 3.0) * 50.0
    return BarFrame(
        symbol="TEST",
        timeframe="1d",
        open=open_[None, :],
        high=high[None, :],
        low=low[None, :],
        close=close[None, :],
        volume=volume[None, :],
        time=x[None, :],
        is_closed=np.ones((1, n_bars), dtype=bool),
        source="test",
    )


def _settings(max_formula_length: int = 5) -> MiningSettings:
    return replace(MiningSettings(), max_formula_length=max_formula_length)


def test_constrained_sampler_has_zero_invalid_rate_and_is_reproducible() -> None:
    first = ConstrainedFormulaSampler(min_length=3, max_length=6, seed=7)
    second = ConstrainedFormulaSampler(min_length=3, max_length=6, seed=7)

    first_samples = first.sample_many(200)
    second_samples = second.sample_many(200)

    assert first_samples == second_samples
    assert len({len(tokens) for tokens in first_samples}) > 1
    assert all(3 <= len(tokens) <= 6 for tokens in first_samples)
    assert all(first.is_valid(tokens) for tokens in first_samples)
    assert not first.is_valid([first.operator_offset])
    assert not first.is_valid([0])


def test_ic_and_rank_ic_detect_positive_and_negative_relationships() -> None:
    x = np.arange(80, dtype=np.float64)
    factor = np.stack([np.sin(x / 5.0), np.cos(x / 7.0)])
    positive = factor * 0.02 + np.sin(x / 13.0) * 0.001
    negative = -positive

    assert compute_ic(factor, positive) > 0.95
    assert compute_rank_ic(factor, positive) > 0.95
    assert compute_ic(factor, negative) < -0.95
    assert compute_rank_ic(factor, negative) < -0.95


def test_reward_includes_cost_correlation_and_repetition_penalties() -> None:
    x = np.arange(120, dtype=np.float64)
    factor = np.stack([np.sin(x / 8.0), np.cos(x / 11.0)])
    target = np.stack([np.sin(x / 8.0) * 0.01, np.cos(x / 11.0) * 0.01])
    no_cost = RewardScorer(RewardConfig(transaction_cost_bps=0.0))
    high_cost = RewardScorer(RewardConfig(transaction_cost_bps=200.0))
    base = no_cost.evaluate(factor, target, formula_tokens=(0, 1, 2))
    expensive = high_cost.evaluate(factor, target, formula_tokens=(0, 1, 2))
    correlated = no_cost.evaluate(
        factor,
        target,
        formula_tokens=(0, 1, 2),
        reference_factors=(factor.copy(),),
    )

    assert expensive.score < base.score
    assert expensive.cost_penalty > base.cost_penalty
    assert correlated.correlation_penalty > 0.0
    assert repetition_penalty((1, 1, 1, 1)) > repetition_penalty((1, 2, 3, 4))


def test_walk_forward_keeps_gap_and_final_holdout_untouched() -> None:
    plan = build_walk_forward_plan(
        260,
        train_bars=80,
        validation_bars=20,
        gap=4,
        folds=3,
        holdout_bars=40,
    )

    assert len(plan.folds) == 3
    assert plan.holdout_start == 220
    assert plan.holdout_end == 260
    for fold in plan.folds:
        assert fold.validation_start == fold.train_end + fold.gap
        assert fold.validation_end <= plan.holdout_start
        assert fold.validation_start >= fold.train_end
    assert all(fold.validation_end <= plan.holdout_start for fold in plan.folds)


def test_forward_labels_only_use_the_declared_slice() -> None:
    frame = _frame(260)
    plan = WalkForwardPlan.build(
        260,
        train_bars=80,
        validation_bars=20,
        gap=4,
        folds=2,
        holdout_bars=40,
    )
    fold = plan.folds[0]
    train_frame = plan.train_frame(frame, fold)
    original = forward_log_returns(train_frame, horizon=2)

    changed_close = frame.close.copy()
    changed_close[:, plan.holdout_start :] *= 1.5
    changed = replace(
        frame,
        close=changed_close,
        high=np.maximum(frame.high, changed_close),
        low=np.minimum(frame.low, changed_close),
    )
    changed_train = forward_log_returns(plan.train_frame(changed, fold), horizon=2)

    np.testing.assert_allclose(original, changed_train, equal_nan=True)
    assert np.isnan(original[0, -2:]).all()
    assert not np.isnan(original[0, :-2]).any()


def test_elite_pool_deduplicates_and_keeps_best_formula() -> None:
    pool = ElitePool(capacity=3)
    assert pool.add(0.2, (1, 2, 3), step=1)
    assert pool.add(0.7, (1, 2, 3), step=2)
    assert pool.add(0.4, (4, 5, 6), step=3)
    assert not pool.add(0.3, (4, 5, 6), step=4)

    assert len(pool) == 2
    assert pool.best is not None
    assert pool.best.formula_tokens == (1, 2, 3)
    assert pool.best.score == 0.7
    weights = pool.replay_weights(current_step=10)
    assert weights.shape == (2,)
    assert np.isclose(weights.sum(), 1.0)


def test_factor_pool_penalizes_repeated_factor_exposure() -> None:
    factor = np.sin(np.arange(80, dtype=np.float64) / 5.0)[None, :]
    pool = FactorPool(capacity=2)
    assert pool.add(1.0, (0,), factor, step=1)
    assert np.isclose(pool.max_correlation(factor), 1.0)
    assert pool.penalty(factor, threshold=0.8, weight=1.0) > 0.0


def test_checkpoint_round_trip_resumes_step_model_pool_and_random_state(
    tmp_path,
) -> None:
    frame = _frame(120)
    settings = _settings()
    run = TrainingRun(
        id="run-1",
        dataset_id="dataset-1",
        symbol=frame.symbol,
        timeframe=frame.timeframe,
    )
    engine = MiningEngine(
        frame,
        run,
        settings=settings,
        batch_size=6,
        elite_pool_size=4,
        seed=11,
    )
    engine.train_step()
    checkpoint = engine.checkpoint()
    checkpoint_path = checkpoint.save(tmp_path / "checkpoint.json")
    loaded = TrainingCheckpoint.load(checkpoint_path)

    assert loaded.step == 1
    assert np.array_equal(loaded.model_state["weights"], engine.policy.weights)
    assert loaded.elite_pool == engine.pool.state_dict()
    assert loaded.factor_pool["capacity"] == engine.factor_pool.capacity
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))["step"] == 1

    control = MiningEngine(
        frame,
        run,
        settings=settings,
        batch_size=6,
        elite_pool_size=4,
        seed=999,
    ).resume(loaded)
    restored = MiningEngine(
        frame,
        run,
        settings=settings,
        batch_size=6,
        elite_pool_size=4,
        seed=123,
    ).resume(TrainingCheckpoint.load(checkpoint_path))

    control_metrics = control.train_step()
    restored_metrics = restored.train_step()

    np.testing.assert_array_equal(control.policy.weights, restored.policy.weights)
    assert control_metrics == restored_metrics
    assert control.pool.state_dict() == restored.pool.state_dict()


def test_training_is_deterministic_and_result_is_serializable() -> None:
    frame = _frame(140)
    run = TrainingRun(
        id="deterministic",
        dataset_id="dataset",
        symbol=frame.symbol,
        timeframe=frame.timeframe,
    )
    first = MiningEngine(
        frame,
        run,
        settings=_settings(),
        batch_size=8,
        elite_pool_size=5,
        seed=23,
    ).train(steps=2)
    second = MiningEngine(
        frame,
        run,
        settings=_settings(),
        batch_size=8,
        elite_pool_size=5,
        seed=23,
    ).train(steps=2)

    assert first.metrics == second.metrics
    assert first.history == second.history
    assert first.best_formula_tokens == second.best_formula_tokens
    assert json.dumps(first.to_dict(), allow_nan=False)

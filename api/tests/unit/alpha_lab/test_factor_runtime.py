from __future__ import annotations

import numpy as np

from xquant.alpha_lab.data import BarFrame
from xquant.alpha_lab.errors import SchemaCompatibilityError
from xquant.alpha_lab.factor import (
    FEATURE_REGISTRY,
    FORMULA_VOCAB,
    OPERATOR_REGISTRY,
    FactorRuntime,
    SignalKernel,
)


def _frame(n_bars: int = 260) -> BarFrame:
    x = np.arange(n_bars, dtype=np.float64)
    close = 100.0 + np.sin(x / 7.0) * 2.0 + x * 0.03
    open_ = close + np.cos(x / 5.0) * 0.2
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = 1_000.0 + np.sin(x / 3.0) * 50.0 + x
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


def test_registry_and_vocabulary_are_deterministic() -> None:
    assert len(FEATURE_REGISTRY.names) == 65
    assert len(OPERATOR_REGISTRY.names) == 62
    assert FORMULA_VOCAB.schema_version.startswith("xqs-v")
    assert len(FORMULA_VOCAB.token_names) == 127


def test_features_are_finite_and_causal() -> None:
    frame = _frame()
    features = FEATURE_REGISTRY.compute(frame)
    assert features.shape == (1, 65, 260)
    assert np.isfinite(features).all()

    changed_close = frame.close.copy()
    changed_close[0, 220:] += 50.0
    changed = BarFrame(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        open=frame.open.copy(),
        high=np.maximum(frame.high, changed_close),
        low=frame.low.copy(),
        close=changed_close,
        volume=frame.volume.copy(),
        time=frame.time.copy(),
        is_closed=frame.is_closed.copy(),
        source=frame.source,
    )
    changed_features = FEATURE_REGISTRY.compute(changed)
    assert np.allclose(features[..., :220], changed_features[..., :220], equal_nan=False)


def test_stack_vm_and_signal_kernel_share_schema() -> None:
    frame = _frame()
    features = FEATURE_REGISTRY.compute(frame)
    runtime = FactorRuntime()
    # RET -> TS_MEAN_5
    formula = [0, len(FEATURE_REGISTRY.names) + OPERATOR_REGISTRY.names.index("TS_MEAN_5")]
    factors = runtime.evaluate(formula, features, FORMULA_VOCAB.schema_version)
    assert factors.shape == (1, frame.n_bars)
    decision = SignalKernel().evaluate_last(
        factors=factors,
        strategy_id="test",
        strategy_version="1.0.0",
        formula_tokens=tuple(formula),
        factor_schema_version=FORMULA_VOCAB.schema_version,
        bar_time="2026-01-01",
    )
    assert decision.bars_used == frame.n_bars
    assert decision.direction.value in {"LONG", "SHORT", "FLAT"}
    assert 0 <= decision.strength <= 1


def test_schema_mismatch_hard_fails() -> None:
    frame = _frame()
    features = FEATURE_REGISTRY.compute(frame)
    runtime = FactorRuntime()
    try:
        runtime.evaluate([0], features, "v-incompatible")
    except SchemaCompatibilityError as exc:
        assert "schema mismatch" in str(exc)
    else:
        raise AssertionError("schema mismatch must fail")

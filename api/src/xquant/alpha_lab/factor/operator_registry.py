from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

OperatorFn = Callable[..., np.ndarray]


def _finite(values: np.ndarray) -> np.ndarray:
    return np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)


def _safe_div(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    safe = np.where(
        np.abs(denominator) < 1e-6,
        np.where(denominator < 0, -1e-6, 1e-6),
        denominator,
    )
    return _finite(numerator / safe)


def _rolling(values: np.ndarray, window: int) -> np.ndarray:
    pad = np.zeros((*values.shape[:-1], window - 1), dtype=np.float64)
    padded = np.concatenate([pad, values], axis=-1)
    return np.lib.stride_tricks.sliding_window_view(padded, window, axis=-1)


def _delay(values: np.ndarray, periods: int) -> np.ndarray:
    output = np.zeros_like(values)
    if periods < values.shape[-1]:
        output[..., periods:] = values[..., :-periods]
    return output


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling(values, window).mean(axis=-1)


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling(values, window).std(axis=-1) + 1e-9


def _rolling_rank(values: np.ndarray, window: int) -> np.ndarray:
    result = np.zeros_like(values)
    windows = _rolling(values, window)
    for t in range(values.shape[-1]):
        for n in range(values.shape[0]):
            result[n, t] = np.mean(windows[n, t] <= values[n, t])
    return result


def _rolling_corr(x: np.ndarray, y: np.ndarray, window: int) -> np.ndarray:
    xw = _rolling(x, window)
    yw = _rolling(y, window)
    xm = xw.mean(axis=-1, keepdims=True)
    ym = yw.mean(axis=-1, keepdims=True)
    covariance = np.mean((xw - xm) * (yw - ym), axis=-1)
    return _safe_div(covariance, xw.std(axis=-1) * yw.std(axis=-1) + 1e-8)


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    result = np.zeros_like(values)
    result[..., 0] = values[..., 0]
    for index in range(1, values.shape[-1]):
        result[..., index] = alpha * values[..., index] + (1.0 - alpha) * result[..., index - 1]
    return result


def _ts_quantile(values: np.ndarray, window: int) -> np.ndarray:
    return np.quantile(_rolling(values, window), 0.5, axis=-1)


def _ts_skew(values: np.ndarray, window: int) -> np.ndarray:
    windows = _rolling(values, window)
    mean = windows.mean(axis=-1, keepdims=True)
    std = windows.std(axis=-1, keepdims=True) + 1e-9
    return np.mean(((windows - mean) / std) ** 3, axis=-1)


def _ts_argmax(values: np.ndarray, window: int) -> np.ndarray:
    return np.argmax(_rolling(values, window), axis=-1).astype(np.float64) / max(1, window - 1)


def _ts_argmin(values: np.ndarray, window: int) -> np.ndarray:
    return np.argmin(_rolling(values, window), axis=-1).astype(np.float64) / max(1, window - 1)


def _decay_linear(values: np.ndarray, window: int) -> np.ndarray:
    weights = np.arange(1, window + 1, dtype=np.float64)
    return np.sum(_rolling(values, window) * weights, axis=-1) / weights.sum()


def _product(values: np.ndarray, window: int) -> np.ndarray:
    return np.prod(_rolling(values, window), axis=-1)


def _zscore(values: np.ndarray, window: int) -> np.ndarray:
    mean = _rolling_mean(values, window)
    std = _rolling_std(values, window)
    return _safe_div(values - mean, std)


def _winsorize(values: np.ndarray) -> np.ndarray:
    windows = _rolling(values, 20)
    lower = np.quantile(windows, 0.05, axis=-1)
    upper = np.quantile(windows, 0.95, axis=-1)
    return np.clip(values, lower, upper)


def _gate(condition: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.where(condition > 0, x, y)


def _jump(values: np.ndarray) -> np.ndarray:
    return np.tanh(values - _delay(values, 1))


def _decay(values: np.ndarray) -> np.ndarray:
    return 0.5 * values + 0.5 * _delay(values, 1)


def _wma(values: np.ndarray) -> np.ndarray:
    return (4.0 * values + 3.0 * _delay(values, 1) + 2.0 * _delay(values, 2) + _delay(values, 3)) / 10.0


def _delta(values: np.ndarray, periods: int) -> np.ndarray:
    return values - _delay(values, periods)


def _scale(values: np.ndarray) -> np.ndarray:
    denominator = np.sum(np.abs(values), axis=-1, keepdims=True) + 1e-9
    return _safe_div(values, denominator)


def _signed_power(values: np.ndarray, power: float) -> np.ndarray:
    return np.sign(values) * np.abs(values) ** power


def _decay_exp(values: np.ndarray, window: int, decay: float) -> np.ndarray:
    weights = decay ** np.arange(window, dtype=np.float64)
    return np.sum(_rolling(values, window) * weights, axis=-1) / (weights.sum() + 1e-9)


def _cross_sectional(values: np.ndarray, mode: str) -> np.ndarray:
    if values.shape[0] == 1:
        return np.full_like(values, 0.5) if mode in {"rank", "scale"} else np.zeros_like(values)
    if mode == "rank":
        return np.argsort(np.argsort(values, axis=0), axis=0) / (values.shape[0] - 1)
    if mode == "scale":
        minimum = values.min(axis=0, keepdims=True)
        maximum = values.max(axis=0, keepdims=True)
        return _safe_div(values - minimum, maximum - minimum)
    return values - values.mean(axis=0, keepdims=True)


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    arity: int
    version: str
    category: str
    execute: OperatorFn
    safe_mode: str = "finite"


class OperatorRegistry:
    def __init__(self) -> None:
        self._operators: list[OperatorSpec] = []
        self._by_name: dict[str, OperatorSpec] = {}

    def register(self, spec: OperatorSpec) -> None:
        if spec.name in self._by_name:
            raise ValueError(f"operator already registered: {spec.name}")
        if spec.arity not in {1, 2, 3}:
            raise ValueError(f"unsupported operator arity: {spec.name}")
        self._operators.append(spec)
        self._by_name[spec.name] = spec

    @property
    def specs(self) -> tuple[OperatorSpec, ...]:
        return tuple(self._operators)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._operators)

    def get(self, name: str) -> OperatorSpec:
        return self._by_name[name]


def _build_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    definitions: list[tuple[str, int, OperatorFn]] = [
        ("ADD", 2, lambda x, y: x + y),
        ("SUB", 2, lambda x, y: x - y),
        ("MUL", 2, lambda x, y: x * y),
        ("DIV", 2, _safe_div),
        ("NEG", 1, lambda x: -x),
        ("ABS", 1, np.abs),
        ("SIGN", 1, np.sign),
        ("GATE", 3, _gate),
        ("JUMP", 1, _jump),
        ("DECAY", 1, _decay),
        ("DELAY1", 1, lambda x: _delay(x, 1)),
        ("MAX3", 1, lambda x: np.maximum.reduce([x, _delay(x, 1), _delay(x, 2)])),
        ("TS_MEAN_5", 1, lambda x: _rolling_mean(x, 5)),
        ("TS_MEAN_10", 1, lambda x: _rolling_mean(x, 10)),
        ("TS_MEAN_20", 1, lambda x: _rolling_mean(x, 20)),
        ("TS_STD_5", 1, lambda x: _rolling_std(x, 5)),
        ("TS_STD_10", 1, lambda x: _rolling_std(x, 10)),
        ("TS_STD_20", 1, lambda x: _rolling_std(x, 20)),
        ("TS_RANK_5", 1, lambda x: _rolling_rank(x, 5)),
        ("TS_RANK_10", 1, lambda x: _rolling_rank(x, 10)),
        ("TS_RANK_20", 1, lambda x: _rolling_rank(x, 20)),
        ("TS_CORR_10", 2, lambda x, y: _rolling_corr(x, y, 10)),
        ("MOMENTUM_5", 1, lambda x: _rolling_mean(x, 5) - _rolling_mean(x, 20)),
        ("MOMENTUM_10", 1, lambda x: _rolling_mean(x, 10) - _rolling_mean(x, 20)),
        ("TS_MAX_10", 1, lambda x: _rolling(x, 10).max(axis=-1)),
        ("TS_MIN_10", 1, lambda x: _rolling(x, 10).min(axis=-1)),
        ("WMA", 1, _wma),
        ("DELAY4", 1, lambda x: _delay(x, 4)),
        ("EMA_5", 1, lambda x: _ema(x, 5)),
        ("EMA_20", 1, lambda x: _ema(x, 20)),
        ("TS_QUANTILE_10", 1, lambda x: _ts_quantile(x, 10)),
        ("TS_SKEW_10", 1, lambda x: _ts_skew(x, 10)),
        ("TS_MIN_20", 1, lambda x: _rolling(x, 20).min(axis=-1)),
        ("TS_MAX_20", 1, lambda x: _rolling(x, 20).max(axis=-1)),
        ("DELTA", 1, lambda x: _delta(x, 1)),
        ("TS_ARG_MAX_5", 1, lambda x: _ts_argmax(x, 5)),
        ("TS_ARG_MIN_5", 1, lambda x: _ts_argmin(x, 5)),
        ("DECAY_LINEAR_5", 1, lambda x: _decay_linear(x, 5)),
        ("SCALE", 1, _scale),
        ("COVARIANCE_10", 2, lambda x, y: _rolling_mean(x * y, 10) - _rolling_mean(x, 10) * _rolling_mean(y, 10)),
        ("PRODUCT_5", 1, lambda x: _product(x, 5)),
        ("SIGNED_POWER_2", 1, lambda x: _signed_power(x, 2.0)),
        ("TS_DECAY_EXP_5", 1, lambda x: _decay_exp(x, 5, 0.5)),
        ("DELTA_5", 1, lambda x: _delta(x, 5)),
        ("CS_RANK", 1, lambda x: _cross_sectional(x, "rank")),
        ("CS_SCALE", 1, lambda x: _cross_sectional(x, "scale")),
        ("CS_NEUTRALIZE", 1, lambda x: _cross_sectional(x, "neutralize")),
        ("TS_SUM_5", 1, lambda x: _rolling(x, 5).sum(axis=-1)),
        ("TS_SUM_10", 1, lambda x: _rolling(x, 10).sum(axis=-1)),
        ("TS_SUM_20", 1, lambda x: _rolling(x, 20).sum(axis=-1)),
        ("MIN", 2, np.minimum),
        ("MAX", 2, np.maximum),
        ("POWER", 1, lambda x: _signed_power(x, 2.0)),
        ("SIGNED_LOG", 1, lambda x: np.sign(x) * np.log1p(np.abs(x))),
        ("SQRT", 1, lambda x: np.sign(x) * np.sqrt(np.abs(x))),
        ("TS_ZSCORE_10", 1, lambda x: _zscore(x, 10)),
        ("TS_ZSCORE_20", 1, lambda x: _zscore(x, 20)),
        ("WINSORIZE", 1, _winsorize),
        ("CLIP", 1, lambda x: np.clip(x, -3.0, 3.0)),
        ("SIGMOID", 1, lambda x: 2.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0))) - 1.0),
        ("TANH_SQUASH", 1, np.tanh),
        ("IF_GT", 3, lambda x, y, z: np.where(x > 0, y, z)),
    ]
    for name, arity, execute in definitions:
        registry.register(
            OperatorSpec(
                name=name,
                arity=arity,
                version="1.0",
                category="core",
                execute=lambda *args, fn=execute: _finite(fn(*args)),
            )
        )
    return registry


OPERATOR_REGISTRY = _build_registry()

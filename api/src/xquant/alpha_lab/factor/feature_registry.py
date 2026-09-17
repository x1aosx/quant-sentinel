from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..data import BarFrame

FeatureCompute = Callable[[BarFrame], np.ndarray]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    version: str
    lookback: int
    category: str
    causal: bool
    compute: FeatureCompute


class FeatureRegistry:
    def __init__(self) -> None:
        self._features: list[FeatureSpec] = []
        self._by_name: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> None:
        if spec.name in self._by_name:
            raise ValueError(f"feature already registered: {spec.name}")
        if not spec.causal:
            raise ValueError(f"feature must be causal: {spec.name}")
        self._features.append(spec)
        self._by_name[spec.name] = spec

    @property
    def specs(self) -> tuple[FeatureSpec, ...]:
        return tuple(self._features)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self._features)

    def get(self, name: str) -> FeatureSpec:
        return self._by_name[name]

    def compute(self, frame: BarFrame) -> np.ndarray:
        arrays = [spec.compute(frame) for spec in self._features]
        result = np.stack(arrays, axis=1)
        if result.shape != (frame.n_symbols, len(self._features), frame.n_bars):
            raise ValueError(
                "feature registry produced an invalid tensor shape: "
                f"{result.shape}"
            )
        return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def _safe_div(numerator: np.ndarray, denominator: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    safe = np.where(
        np.abs(denominator) < eps,
        np.where(denominator < 0, -eps, eps),
        denominator,
    )
    return numerator / safe


def _pad_zero(values: np.ndarray, width: int) -> np.ndarray:
    if values.shape[-1] == 0:
        return values
    pad = np.zeros((*values.shape[:-1], width), dtype=np.float64)
    return np.concatenate([pad, values], axis=-1)


def _rolling(values: np.ndarray, window: int) -> np.ndarray:
    padded = _pad_zero(values, window - 1)
    return np.lib.stride_tricks.sliding_window_view(padded, window, axis=-1)


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling(values, window).mean(axis=-1)


def _rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling(values, window).sum(axis=-1)


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    windows = _rolling(values, window)
    return windows.std(axis=-1) + 1e-9


def _rolling_min(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling(values, window).min(axis=-1)


def _rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling(values, window).max(axis=-1)


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    output = np.zeros_like(values)
    output[..., 0] = values[..., 0]
    for index in range(1, values.shape[-1]):
        output[..., index] = alpha * values[..., index] + (1.0 - alpha) * output[..., index - 1]
    return output


def _diff(values: np.ndarray, periods: int = 1) -> np.ndarray:
    output = np.zeros_like(values)
    if periods < values.shape[-1]:
        output[..., periods:] = values[..., periods:] - values[..., :-periods]
    return output


def _returns(close: np.ndarray) -> np.ndarray:
    return np.nan_to_num(_diff(np.log(np.maximum(close, 1e-12))))


def _slope(values: np.ndarray, window: int) -> np.ndarray:
    windows = _rolling(values, window)
    index = np.arange(window, dtype=np.float64)
    centered = index - index.mean()
    denominator = np.sum(centered**2) + 1e-9
    return np.sum((windows - windows.mean(axis=-1, keepdims=True)) * centered, axis=-1) / denominator


def _linear_slope(values: np.ndarray, window: int) -> np.ndarray:
    mean = _rolling_mean(values, window)
    return _safe_div(_slope(values, window), np.abs(mean) + 1e-9)


def _rolling_corr(x: np.ndarray, y: np.ndarray, window: int) -> np.ndarray:
    xw = _rolling(x, window)
    yw = _rolling(y, window)
    xm = xw.mean(axis=-1, keepdims=True)
    ym = yw.mean(axis=-1, keepdims=True)
    covariance = np.mean((xw - xm) * (yw - ym), axis=-1)
    denominator = xw.std(axis=-1) * yw.std(axis=-1) + 1e-8
    return np.nan_to_num(_safe_div(covariance, denominator))


def _rsi(close: np.ndarray, window: int = 14) -> np.ndarray:
    diff = _diff(close)
    gain = np.maximum(diff, 0.0)
    loss = np.maximum(-diff, 0.0)
    rs = _safe_div(_rolling_mean(gain, window), _rolling_mean(loss, window))
    return np.where((gain == 0) & (loss == 0), 0.0, (100.0 - 100.0 / (1.0 + rs)) / 100.0)


def _atr(frame: BarFrame, window: int = 14) -> np.ndarray:
    previous_close = np.concatenate([frame.close[..., :1], frame.close[..., :-1]], axis=-1)
    true_range = np.maximum.reduce(
        [
            frame.high - frame.low,
            np.abs(frame.high - previous_close),
            np.abs(frame.low - previous_close),
        ]
    )
    return _rolling_mean(true_range, window)


def _rolling_skew(values: np.ndarray, window: int) -> np.ndarray:
    windows = _rolling(values, window)
    mean = windows.mean(axis=-1, keepdims=True)
    std = windows.std(axis=-1, keepdims=True) + 1e-9
    return np.mean(((windows - mean) / std) ** 3, axis=-1)


def _rolling_kurt(values: np.ndarray, window: int) -> np.ndarray:
    windows = _rolling(values, window)
    mean = windows.mean(axis=-1, keepdims=True)
    std = windows.std(axis=-1, keepdims=True) + 1e-9
    return np.mean(((windows - mean) / std) ** 4, axis=-1) - 3.0


def _rolling_entropy(values: np.ndarray, window: int, bins: int = 8) -> np.ndarray:
    windows = _rolling(values, window)
    result = np.zeros(values.shape, dtype=np.float64)
    for t in range(values.shape[-1]):
        for n in range(values.shape[0]):
            sample = windows[n, t]
            counts, _ = np.histogram(sample, bins=bins)
            probabilities = counts[counts > 0] / max(1, counts.sum())
            result[n, t] = -np.sum(probabilities * np.log(probabilities + 1e-12))
    return result / np.log(bins)


def _bollinger(close: np.ndarray, window: int = 20) -> tuple[np.ndarray, np.ndarray]:
    mid = _rolling_mean(close, window)
    std = _rolling_std(close, window)
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std
    pos = np.clip(_safe_div(close - lower, upper - lower), 0.0, 1.0) * 2.0 - 1.0
    width = _safe_div(upper - lower, np.abs(mid) + 1e-9)
    return pos, width


def _vwap_dev(frame: BarFrame, window: int = 20) -> np.ndarray:
    typical = (frame.high + frame.low + frame.close) / 3.0
    numerator = _rolling_sum(typical * frame.volume, window)
    denominator = _rolling_sum(frame.volume, window)
    vwap = _safe_div(numerator, denominator)
    return _safe_div(frame.close - vwap, vwap)


def _obv_slope(frame: BarFrame, window: int = 20) -> np.ndarray:
    direction = np.sign(_diff(frame.close))
    obv = np.cumsum(direction * frame.volume, axis=-1)
    return _linear_slope(obv, window)


def _mfi(frame: BarFrame, window: int = 14) -> np.ndarray:
    typical = (frame.high + frame.low + frame.close) / 3.0
    previous = np.concatenate([typical[..., :1], typical[..., :-1]], axis=-1)
    money_flow = typical * frame.volume
    positive = np.where(typical > previous, money_flow, 0.0)
    negative = np.where(typical < previous, money_flow, 0.0)
    ratio = _safe_div(_rolling_sum(positive, window), _rolling_sum(negative, window))
    return (100.0 - 100.0 / (1.0 + ratio)) / 50.0 - 1.0


def _williams_r(frame: BarFrame, window: int = 14) -> np.ndarray:
    highest = _rolling_max(frame.high, window)
    lowest = _rolling_min(frame.low, window)
    return np.clip(_safe_div(highest - frame.close, highest - lowest), 0.0, 1.0) * 2.0 - 1.0


def _cci(frame: BarFrame, window: int = 14) -> np.ndarray:
    typical = (frame.high + frame.low + frame.close) / 3.0
    mean = _rolling_mean(typical, window)
    mad = _rolling(np.abs(typical - mean), window).mean(axis=-1)
    return np.clip(_safe_div(typical - mean, 0.015 * mad + 1e-9) / 200.0, -1.0, 1.0)


def _true_range(frame: BarFrame) -> np.ndarray:
    previous_close = np.concatenate([frame.close[..., :1], frame.close[..., :-1]], axis=-1)
    return np.maximum.reduce(
        [
            frame.high - frame.low,
            np.abs(frame.high - previous_close),
            np.abs(frame.low - previous_close),
        ]
    )


def _ultimate_oscillator(frame: BarFrame) -> np.ndarray:
    previous_close = np.concatenate([frame.close[..., :1], frame.close[..., :-1]], axis=-1)
    true_low = np.minimum(frame.low, previous_close)
    true_high = np.maximum(frame.high, previous_close)
    buying_pressure = frame.close - true_low
    true_range = true_high - true_low

    def average(window: int) -> np.ndarray:
        return _safe_div(_rolling_sum(buying_pressure, window), _rolling_sum(true_range, window))

    value = (4.0 * average(7) + 2.0 * average(14) + average(28)) / 7.0
    return np.clip(value * 2.0 - 1.0, -1.0, 1.0)


def _dmi(frame: BarFrame, window: int = 14) -> tuple[np.ndarray, np.ndarray]:
    up = _diff(frame.high)
    down = -_diff(frame.low)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = _rolling_mean(_true_range(frame), window) + 1e-9
    plus_di = 100.0 * _safe_div(_rolling_mean(plus_dm, window), atr)
    minus_di = 100.0 * _safe_div(_rolling_mean(minus_dm, window), atr)
    dx = 100.0 * _safe_div(np.abs(plus_di - minus_di), plus_di + minus_di + 1e-9)
    return dx, plus_di - minus_di


def _cross_sectional(values: np.ndarray, mode: str) -> np.ndarray:
    if values.shape[0] == 1:
        if mode == "rank":
            return np.full_like(values, 0.5)
        if mode == "scale":
            return np.full_like(values, 0.5)
        return np.zeros_like(values)
    if mode == "rank":
        order = np.argsort(np.argsort(values, axis=0), axis=0)
        return order / max(1, values.shape[0] - 1)
    if mode == "scale":
        minimum = values.min(axis=0, keepdims=True)
        maximum = values.max(axis=0, keepdims=True)
        return _safe_div(values - minimum, maximum - minimum)
    return values - values.mean(axis=0, keepdims=True)


def _feature(
    name: str,
    category: str,
    compute: FeatureCompute,
    lookback: int,
) -> FeatureSpec:
    return FeatureSpec(
        name=name,
        version="1.0",
        lookback=lookback,
        category=category,
        causal=True,
        compute=compute,
    )


def _build_registry() -> FeatureRegistry:
    registry = FeatureRegistry()
    definitions: list[tuple[str, str, FeatureCompute, int]] = [
        ("RET", "trend", lambda f: _returns(f.close), 2),
        ("RET5", "trend", lambda f: _safe_div(f.close, _pad_zero(f.close[..., :-5], 5)), 5),
        ("RET20", "trend", lambda f: _safe_div(f.close, _pad_zero(f.close[..., :-20], 20)), 20),
        ("MA_DIFF", "trend", lambda f: _safe_div(f.close - _rolling_mean(f.close, 20), _rolling_mean(f.close, 20)), 20),
        ("SLOPE20", "trend", lambda f: _linear_slope(f.close, 20), 20),
        ("ATR", "volatility", lambda f: _safe_div(_atr(f), f.close), 14),
        ("RVOL", "volatility", lambda f: _rolling_std(_returns(f.close), 20), 20),
        ("HL_RANGE", "volatility", lambda f: _safe_div(f.high - f.low, f.close), 1),
        ("VOL_REGIME", "volatility", lambda f: _safe_div(_rolling_std(_returns(f.close), 20), _rolling_mean(_rolling_std(_returns(f.close), 20), 60) + 1e-9), 60),
        ("DEV", "reversal", lambda f: _safe_div(f.close - _rolling_mean(f.close, 20), _rolling_mean(f.close, 20)), 20),
        ("DEV60", "reversal", lambda f: _safe_div(f.close - _rolling_mean(f.close, 60), _rolling_mean(f.close, 60)), 60),
        ("RSI14", "reversal", lambda f: _rsi(f.close), 14),
        ("PRESSURE", "reversal", lambda f: _safe_div(2.0 * f.close - f.high - f.low, f.high - f.low), 1),
        ("AC1", "reversal", lambda f: _rolling_corr(_returns(f.close), _pad_zero(_returns(f.close)[..., :-1], 1), 20), 20),
        ("VOL_RATIO", "volume", lambda f: _safe_div(f.volume, _rolling_mean(f.volume, 20)) - 1.0, 20),
        ("VOL_Z", "volume", lambda f: _safe_div(f.volume - _rolling_mean(f.volume, 20), _rolling_std(f.volume, 20)), 20),
        ("PV_CORR", "volume", lambda f: _rolling_corr(_returns(f.close), _returns(f.volume), 20), 20),
        ("REL_RET5", "cross_sectional", lambda f: _cross_sectional(_safe_div(f.close, _pad_zero(f.close[..., :-5], 5)) - 1.0, "neutralize"), 5),
        ("REL_RET20", "cross_sectional", lambda f: _cross_sectional(_safe_div(f.close, _pad_zero(f.close[..., :-20], 20)) - 1.0, "neutralize"), 20),
        ("REL_VOL", "cross_sectional", lambda f: _cross_sectional(_rolling_std(_returns(f.close), 20), "neutralize"), 20),
        ("VWAP_DEV", "volume", _vwap_dev, 20),
        ("BOLL_POS", "channel", lambda f: _bollinger(f.close)[0], 20),
        ("BOLL_WIDTH", "volatility", lambda f: _bollinger(f.close)[1], 20),
        ("MACD_HIST", "momentum", lambda f: _safe_div(_ema(f.close, 12) - _ema(f.close, 26) - _ema(_ema(f.close, 12) - _ema(f.close, 26), 9), f.close), 35),
        ("OBV_SLOPE", "volume", _obv_slope, 20),
        ("MFI14", "volume", _mfi, 14),
        ("WILLR_14", "reversal", _williams_r, 14),
        ("CCI_14", "reversal", _cci, 14),
        ("ROC_12", "momentum", lambda f: _safe_div(f.close, _pad_zero(f.close[..., :-12], 12)) - 1.0, 12),
        ("TYPICAL_DEV", "reversal", lambda f: _safe_div((f.high + f.low + f.close) / 3.0 - _rolling_mean((f.high + f.low + f.close) / 3.0, 20), _rolling_mean((f.high + f.low + f.close) / 3.0, 20)), 20),
        ("EMA_RATIO_12_26", "trend", lambda f: _safe_div(_ema(f.close, 12), _ema(f.close, 26)) - 1.0, 26),
        ("TREND_STRENGTH_50", "trend", lambda f: _linear_slope(f.close, 50), 50),
        ("PRICE_POS_50", "trend", lambda f: _safe_div(f.close - _rolling_min(f.close, 50), _rolling_max(f.close, 50) - _rolling_min(f.close, 50)), 50),
        ("TRIX_15", "momentum", lambda f: _safe_div(_ema(_ema(_ema(f.close, 15), 15), 15) - _pad_zero(_ema(_ema(_ema(f.close, 15), 15), 15)[..., :-1], 1), np.abs(_ema(_ema(_ema(f.close, 15), 15), 15)) + 1e-9), 15),
        ("PPO", "momentum", lambda f: _safe_div(_ema(f.close, 12) - _ema(f.close, 26), _ema(f.close, 26)), 26),
        ("ULT_OSC", "momentum", _ultimate_oscillator, 28),
        ("RET_ACCEL", "momentum", lambda f: _diff(_returns(f.close)), 2),
        ("GK_VOL", "volatility", lambda f: np.sqrt(np.maximum(0.5 * np.log(f.high / f.low) ** 2 - (2.0 * np.log(2.0) - 1.0) * _returns(f.close) ** 2, 0.0)), 2),
        ("PARKINSON_VOL", "volatility", lambda f: np.sqrt(np.maximum(np.log(f.high / f.low) ** 2 / (4.0 * np.log(2.0)), 0.0)), 2),
        ("YANG_ZHANG_VOL", "volatility", lambda f: np.sqrt(np.maximum(_rolling_std(_returns(f.close), 20) ** 2 + _rolling_std(np.log(f.open / np.concatenate([f.open[..., :1], f.close[..., :-1]], axis=-1)), 20) ** 2, 0.0)), 20),
        ("RS_VOL", "volatility", lambda f: np.sqrt(np.maximum(_rolling_mean(np.log(f.high / f.close) * np.log(f.high / f.open) + np.log(f.low / f.close) * np.log(f.low / f.open), 20), 0.0)), 20),
        ("AMIHUD_ILLIQ", "volume", lambda f: _safe_div(np.abs(_returns(f.close)), np.maximum(f.volume * f.close, 1e-9)), 2),
        ("KYLE_LAMBDA", "volume", lambda f: _safe_div(_returns(f.close), np.maximum(f.volume, 1e-9)), 2),
        ("CMF_20", "volume", lambda f: _safe_div(_rolling_sum(((f.close - f.low) - (f.high - f.close)) / (f.high - f.low + 1e-9) * f.volume, 20), _rolling_sum(f.volume, 20)), 20),
        ("AD_LINE_SLOPE", "volume", lambda f: _linear_slope(np.cumsum(_safe_div(2.0 * f.close - f.high - f.low, f.high - f.low) * f.volume, axis=-1), 20), 20),
        ("STOCH_K_14", "reversal", lambda f: _safe_div(f.close - _rolling_min(f.low, 14), _rolling_max(f.high, 14) - _rolling_min(f.low, 14)) * 2.0 - 1.0, 14),
        ("STOCH_D_3", "reversal", lambda f: _rolling_mean(_safe_div(f.close - _rolling_min(f.low, 14), _rolling_max(f.high, 14) - _rolling_min(f.low, 14)) * 2.0 - 1.0, 3), 14),
        ("AROON_OSC_25", "reversal", lambda f: _safe_div(np.argmax(_rolling(f.high, 25), axis=-1) - np.argmin(_rolling(f.low, 25), axis=-1), 24.0), 25),
        ("DMI_ADX_14", "trend", lambda f: _dmi(f)[0] / 100.0, 14),
        ("DMI_DIFF_14", "trend", lambda f: _dmi(f)[1] / 100.0, 14),
        ("TRIX_SIGNAL", "momentum", lambda f: _rolling_mean(_safe_div(_ema(_ema(_ema(f.close, 15), 15), 15) - _pad_zero(_ema(_ema(_ema(f.close, 15), 15), 15)[..., :-1], 1), np.abs(_ema(_ema(_ema(f.close, 15), 15), 15)) + 1e-9), 9), 15),
        ("DONCHIAN_POS_20", "channel", lambda f: _safe_div(f.close - _rolling_min(f.low, 20), _rolling_max(f.high, 20) - _rolling_min(f.low, 20)), 20),
        ("KELTNER_POS_20", "channel", lambda f: _safe_div(f.close - (_rolling_mean(f.close, 20) + 2.0 * _atr(f)), 4.0 * _atr(f) + 1e-9), 20),
        ("ICHIMOKU_KIJUN_DEV", "channel", lambda f: _safe_div(f.close - (_rolling_max(f.high, 26) + _rolling_min(f.low, 26)) / 2.0, _rolling_max(f.high, 26) - _rolling_min(f.low, 26) + 1e-9), 26),
        ("ICHIMOKU_TENKAN_DEV", "channel", lambda f: _safe_div(f.close - (_rolling_max(f.high, 9) + _rolling_min(f.low, 9)) / 2.0, _rolling_max(f.high, 9) - _rolling_min(f.low, 9) + 1e-9), 9),
        ("SUPERTREND_DIR", "channel", lambda f: np.where(f.close >= _rolling_mean(f.close, 20) + 2.0 * _atr(f), 1.0, np.where(f.close <= _rolling_mean(f.close, 20) - 2.0 * _atr(f), -1.0, 0.0)), 20),
        ("SAR_DIST", "channel", lambda f: _safe_div(f.close - (_rolling_max(f.high, 20) + _rolling_min(f.low, 20)) / 2.0, _rolling_max(f.high, 20) - _rolling_min(f.low, 20) + 1e-9), 20),
        ("ROLL_SKEW_20", "statistical", lambda f: _rolling_skew(_returns(f.close), 20), 20),
        ("ROLL_KURT_20", "statistical", lambda f: _rolling_kurt(_returns(f.close), 20), 20),
        ("HURST_50", "statistical", lambda f: np.clip(0.5 + 0.5 * _rolling_corr(_returns(f.close), _pad_zero(_returns(f.close)[..., :-5], 5), 50), 0.0, 1.0), 50),
        ("FRACTAL_DIM_30", "statistical", lambda f: 2.0 - np.abs(_linear_slope(np.log(np.maximum(f.close, 1e-9)), 30)), 30),
        ("AC2", "statistical", lambda f: _rolling_corr(_returns(f.close), _pad_zero(_returns(f.close)[..., :-2], 2), 20), 20),
        ("RET_ENTROPY_20", "statistical", lambda f: _rolling_entropy(_returns(f.close), 20), 20),
        ("CS_RANK_RET5", "cross_sectional", lambda f: _cross_sectional(_safe_div(f.close, _pad_zero(f.close[..., :-5], 5)) - 1.0, "rank"), 5),
        ("CS_ZSCORE_RET20", "cross_sectional", lambda f: _cross_sectional(_safe_div(f.close, _pad_zero(f.close[..., :-20], 20)) - 1.0, "neutralize"), 20),
    ]
    for name, category, compute, lookback in definitions:
        registry.register(_feature(name, category, compute, lookback))
    return registry


FEATURE_REGISTRY = _build_registry()

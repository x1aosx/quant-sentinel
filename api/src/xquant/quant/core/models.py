"""Cross-layer dataclasses for quantitative analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

ROLE_NAMES = ("strategic", "tactical", "confirmation", "execution")
DEFAULT_ROLE_TIMEFRAMES = {
    "strategic": "1d",
    "tactical": "1h",
    "confirmation": "30m",
    "execution": "15m",
}
DEFAULT_ROLE_WEIGHTS = {
    "strategic": 0.4,
    "tactical": 0.3,
    "confirmation": 0.2,
    "execution": 0.1,
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _float(
    value: Any,
    *,
    default: float = 0.0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _float_list(values: Any) -> list[float]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def _string_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return [str(value) for value in values if value is not None]


@dataclass
class TimeframeAnalysisResult:
    symbol: str = ""
    timeframe: str = ""
    timestamp: str = ""
    trend: str = "UNKNOWN"
    trend_score: float = 0.0
    phase: str = "UNKNOWN"
    momentum_score: float = 0.0
    volatility_score: float = 0.0
    support_levels: list[float] = field(default_factory=list)
    resistance_levels: list[float] = field(default_factory=list)
    volume_state: str = "UNKNOWN"
    price_structure: str = "UNKNOWN"
    signals: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = _text(self.symbol)
        self.timeframe = _text(self.timeframe).strip().lower()
        self.timestamp = _text(self.timestamp)
        self.trend = _text(self.trend, "UNKNOWN").strip().upper()
        self.trend_score = _float(self.trend_score, minimum=0.0, maximum=100.0)
        self.phase = _text(self.phase, "UNKNOWN").strip().upper()
        self.momentum_score = _float(self.momentum_score, minimum=0.0, maximum=100.0)
        self.volatility_score = _float(self.volatility_score, minimum=0.0, maximum=100.0)
        self.support_levels = _float_list(self.support_levels)
        self.resistance_levels = _float_list(self.resistance_levels)
        self.volume_state = _text(self.volume_state, "UNKNOWN").strip().upper()
        self.price_structure = _text(self.price_structure, "UNKNOWN").strip().upper()
        self.signals = _string_list(self.signals)
        self.confidence = _float(self.confidence, minimum=0.0, maximum=1.0)
        self.raw_result = dict(self.raw_result) if isinstance(self.raw_result, Mapping) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "trend": self.trend,
            "trend_score": self.trend_score,
            "phase": self.phase,
            "momentum_score": self.momentum_score,
            "volatility_score": self.volatility_score,
            "support_levels": list(self.support_levels),
            "resistance_levels": list(self.resistance_levels),
            "volume_state": self.volume_state,
            "price_structure": self.price_structure,
            "signals": list(self.signals),
            "confidence": self.confidence,
            "raw_result": _json_safe(self.raw_result),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TimeframeAnalysisResult:
        return cls(
            symbol=payload.get("symbol", ""),
            timeframe=payload.get("timeframe", ""),
            timestamp=payload.get("timestamp", ""),
            trend=payload.get("trend", "UNKNOWN"),
            trend_score=payload.get("trend_score", 0.0),
            phase=payload.get("phase", "UNKNOWN"),
            momentum_score=payload.get("momentum_score", 0.0),
            volatility_score=payload.get("volatility_score", 0.0),
            support_levels=payload.get("support_levels", []),
            resistance_levels=payload.get("resistance_levels", []),
            volume_state=payload.get("volume_state", "UNKNOWN"),
            price_structure=payload.get("price_structure", "UNKNOWN"),
            signals=payload.get("signals", []),
            confidence=payload.get("confidence", 0.0),
            raw_result=payload.get("raw_result", {}),
        )


def _role_value(value: Any, default: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("timeframe", value.get("period", default))
    return _text(value, default).strip().lower() or default


@dataclass(init=False)
class MultiTimeframeProfile:
    strategic: str = "1d"
    tactical: str = "1h"
    confirmation: str = "30m"
    execution: str = "15m"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ROLE_WEIGHTS))

    def __init__(
        self,
        strategic: Any = "1d",
        tactical: Any = "1h",
        confirmation: Any = "30m",
        execution: Any = "15m",
        weights: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if isinstance(strategic, Mapping) and payload is None:
            payload = strategic
            strategic, tactical, confirmation, execution = "1d", "1h", "30m", "15m"

        source: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
        nested = source.get("profile")
        if isinstance(nested, Mapping):
            source = {**source, **nested}

        self.strategic = _role_value(source.get("strategic", strategic), "1d")
        self.tactical = _role_value(source.get("tactical", tactical), "1h")
        self.confirmation = _role_value(
            source.get("confirmation", confirmation),
            "30m",
        )
        self.execution = _role_value(source.get("execution", execution), "15m")

        raw_weights: Mapping[str, Any]
        if isinstance(source.get("weights"), Mapping):
            raw_weights = source["weights"]
        elif isinstance(weights, Mapping):
            raw_weights = weights
        else:
            raw_weights = {}
        self.weights = dict(DEFAULT_ROLE_WEIGHTS)
        for role in ROLE_NAMES:
            if role in raw_weights:
                self.weights[role] = max(0.0, _float(raw_weights[role], default=0.0))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> MultiTimeframeProfile:
        return cls(payload=payload or {})

    def role_timeframes(self) -> dict[str, str]:
        return {role: getattr(self, role) for role in ROLE_NAMES}

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.role_timeframes(),
            "weights": dict(self.weights),
        }


@dataclass
class MultiTimeframeResult:
    symbol: str = ""
    timestamp: str = ""
    strategic_trend: str = "UNKNOWN"
    strategic_score: float = 0.0
    intraday_state: str = "UNKNOWN"
    confirmation_state: str = "UNKNOWN"
    execution_state: str = "UNKNOWN"
    alignment_score: float = 0.0
    conflict_score: float = 0.0
    bullish_score: float = 0.0
    bearish_score: float = 0.0
    summary_state: str = "UNKNOWN"
    missing_timeframes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = _text(self.symbol)
        self.timestamp = _text(self.timestamp)
        self.strategic_trend = _text(self.strategic_trend, "UNKNOWN").strip().upper()
        self.strategic_score = _float(self.strategic_score, minimum=0.0, maximum=100.0)
        self.intraday_state = _text(self.intraday_state, "UNKNOWN").strip().upper()
        self.confirmation_state = _text(
            self.confirmation_state,
            "UNKNOWN",
        ).strip().upper()
        self.execution_state = _text(self.execution_state, "UNKNOWN").strip().upper()
        self.alignment_score = _float(self.alignment_score, minimum=0.0, maximum=100.0)
        self.conflict_score = _float(self.conflict_score, minimum=0.0, maximum=100.0)
        self.bullish_score = _float(self.bullish_score, minimum=0.0, maximum=100.0)
        self.bearish_score = _float(self.bearish_score, minimum=0.0, maximum=100.0)
        self.summary_state = _text(self.summary_state, "UNKNOWN").strip().upper()
        self.missing_timeframes = [
            _text(value).strip().lower()
            for value in _string_list(self.missing_timeframes)
            if _text(value).strip()
        ]
        self.metadata = dict(self.metadata) if isinstance(self.metadata, Mapping) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "strategic_trend": self.strategic_trend,
            "strategic_score": self.strategic_score,
            "intraday_state": self.intraday_state,
            "confirmation_state": self.confirmation_state,
            "execution_state": self.execution_state,
            "alignment_score": self.alignment_score,
            "conflict_score": self.conflict_score,
            "bullish_score": self.bullish_score,
            "bearish_score": self.bearish_score,
            "summary_state": self.summary_state,
            "missing_timeframes": list(self.missing_timeframes),
            "metadata": _json_safe(self.metadata),
        }


@dataclass
class QuantDecision:
    action: str = "WATCH"
    confidence: float = 0.0
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    risk_reward: float | None = None
    trigger_conditions: list[str] = field(default_factory=list)
    invalid_conditions: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.action = _text(self.action, "WATCH").strip().upper()
        self.confidence = _float(self.confidence, minimum=0.0, maximum=1.0)
        self.entry = None if self.entry is None else _float(self.entry)
        self.stop = None if self.stop is None else _float(self.stop)
        self.target = None if self.target is None else _float(self.target)
        self.risk_reward = (
            None if self.risk_reward is None else _float(self.risk_reward, minimum=0.0)
        )
        self.trigger_conditions = _string_list(self.trigger_conditions)
        self.invalid_conditions = _string_list(self.invalid_conditions)
        self.risk_flags = _string_list(self.risk_flags)
        self.reason_codes = _string_list(self.reason_codes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "risk_reward": self.risk_reward,
            "trigger_conditions": list(self.trigger_conditions),
            "invalid_conditions": list(self.invalid_conditions),
            "risk_flags": list(self.risk_flags),
            "reason_codes": list(self.reason_codes),
        }


__all__ = [
    "DEFAULT_ROLE_TIMEFRAMES",
    "DEFAULT_ROLE_WEIGHTS",
    "ROLE_NAMES",
    "MultiTimeframeProfile",
    "MultiTimeframeResult",
    "QuantDecision",
    "TimeframeAnalysisResult",
]

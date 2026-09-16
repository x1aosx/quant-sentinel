from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Self, TypeVar

T = TypeVar("T", bound="Serializable")


DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "market_fit": 0.07,
    "theme": 0.12,
    "forward_theme": 0.12,
    "policy": 0.08,
    "global_event": 0.06,
    "sentiment": 0.05,
    "attention_momentum": 0.07,
    "capital": 0.10,
    "price_action": 0.10,
    "alpha": 0.10,
    "fundamental": 0.08,
    "liquidity": 0.05,
    "crowding": 0.10,
    "risk": 0.10,
}

PENALTY_FEATURES = frozenset({"crowding", "risk"})


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | str | None, *, field_name: str = "datetime") -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return utc_now()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO datetime") from exc
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime or ISO datetime string")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=str)]
    return str(value)


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))  # type: ignore[arg-type,return-value]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        if not isinstance(payload, Mapping):
            raise TypeError(f"{cls.__name__} payload must be a mapping")
        return cls(**dict(payload))


class MarketRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"


class CandidateState(str, Enum):
    DISCOVERED = "DISCOVERED"
    WATCH = "WATCH"
    FOCUS = "FOCUS"
    DEEP_ANALYSIS = "DEEP_ANALYSIS"
    SIGNAL_READY = "SIGNAL_READY"
    COOLDOWN = "COOLDOWN"
    REMOVE = "REMOVE"


def _score(value: float, field_name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError(f"{field_name} must be between 0 and 100")
    return number


def _confidence(value: float, field_name: str = "confidence") -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return number


def _text(value: str, field_name: str, *, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def _text_list(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


@dataclass(slots=True)
class ScoreComponent(Serializable):
    name: str
    score: float
    weight: float
    weighted_value: float
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    penalty: bool = False

    def __post_init__(self) -> None:
        self.name = _text(self.name, "name", required=True)
        self.score = _score(self.score, "score")
        self.weight = float(self.weight)
        if not math.isfinite(self.weight) or self.weight < 0:
            raise ValueError("weight must be a finite non-negative number")
        self.weighted_value = float(self.weighted_value)
        if not math.isfinite(self.weighted_value):
            raise ValueError("weighted_value must be finite")
        self.reasons = _text_list(self.reasons)
        self.risks = _text_list(self.risks)
        if not self.reasons:
            self.reasons = [f"{self.name} 缺少明确原因，按当前数据计算"]
        if not self.risks:
            self.risks = [f"{self.name} 未发现额外风险"]


@dataclass(slots=True)
class DiscoveryWeights(Serializable):
    weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SCORE_WEIGHTS)
    )
    model_version: str = "discovery-mvp-v1"

    def __post_init__(self) -> None:
        merged = dict(DEFAULT_SCORE_WEIGHTS)
        merged.update({str(key): float(value) for key, value in self.weights.items()})
        for name, value in merged.items():
            _text(name, "weight name", required=True)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"weight for {name!r} must be non-negative")
        total = sum(merged.values())
        if total <= 0:
            raise ValueError("at least one discovery weight must be positive")
        self.weights = {name: value / total for name, value in merged.items()}
        self.model_version = _text(
            self.model_version, "model_version", required=True
        )

    def weight_for(self, name: str) -> float:
        return self.weights.get(str(name), 0.0)

    def is_penalty(self, name: str) -> bool:
        return str(name) in PENALTY_FEATURES

    def with_overrides(
        self,
        overrides: Mapping[str, float] | None = None,
        *,
        model_version: str | None = None,
    ) -> Self:
        weights = dict(self.weights)
        weights.update(
            {str(key): float(value) for key, value in (overrides or {}).items()}
        )
        return type(self)(
            weights=weights,
            model_version=model_version or self.model_version,
        )


@dataclass(slots=True)
class StockSnapshot(Serializable):
    stock_id: str
    name: str = ""
    as_of: datetime = field(default_factory=utc_now)
    theme_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    score_reasons: dict[str, list[str]] = field(default_factory=dict)
    score_risks: dict[str, list[str]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence: float = 0.5
    eligible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stock_id = _text(self.stock_id, "stock_id", required=True)
        self.name = _text(self.name, "name")
        self.as_of = ensure_utc(self.as_of, field_name="as_of")
        self.theme_ids = _text_list(self.theme_ids)
        self.event_ids = _text_list(self.event_ids)
        self.scores = {
            str(name): _score(value, f"scores.{name}")
            for name, value in self.scores.items()
        }
        self.score_reasons = {
            str(name): _text_list(values)
            for name, values in self.score_reasons.items()
        }
        self.score_risks = {
            str(name): _text_list(values)
            for name, values in self.score_risks.items()
        }
        self.reasons = _text_list(self.reasons)
        self.risks = _text_list(self.risks)
        self.confidence = _confidence(self.confidence)
        self.metadata = dict(self.metadata)


@dataclass(slots=True)
class ThemeSnapshot(Serializable):
    theme_id: str
    name: str = ""
    state: str = "DISCOVERED"
    as_of: datetime = field(default_factory=utc_now)
    stock_ids: list[str] = field(default_factory=list)
    current_heat_score: float = 50.0
    forward_heat_score: float = 50.0
    crowding_score: float = 50.0
    sentiment_score: float = 50.0
    policy_score: float = 50.0
    capital_score: float = 50.0
    attention_momentum_score: float = 50.0
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.theme_id = _text(self.theme_id, "theme_id", required=True)
        self.name = _text(self.name, "name")
        self.state = _text(self.state, "state", required=True).upper()
        self.as_of = ensure_utc(self.as_of, field_name="as_of")
        self.stock_ids = _text_list(self.stock_ids)
        self.current_heat_score = _score(
            self.current_heat_score, "current_heat_score"
        )
        self.forward_heat_score = _score(
            self.forward_heat_score, "forward_heat_score"
        )
        self.crowding_score = _score(self.crowding_score, "crowding_score")
        self.sentiment_score = _score(self.sentiment_score, "sentiment_score")
        self.policy_score = _score(self.policy_score, "policy_score")
        self.capital_score = _score(self.capital_score, "capital_score")
        self.attention_momentum_score = _score(
            self.attention_momentum_score, "attention_momentum_score"
        )
        self.confidence = _confidence(self.confidence)
        self.reasons = _text_list(self.reasons)
        self.risks = _text_list(self.risks)
        self.metadata = dict(self.metadata)


@dataclass(slots=True)
class EventSnapshot(Serializable):
    event_id: str
    title: str = ""
    event_type: str = "OTHER"
    direction: str = "NEUTRAL"
    horizon: str = "SHORT"
    as_of: datetime = field(default_factory=utc_now)
    affected_stock_ids: list[str] = field(default_factory=list)
    affected_theme_ids: list[str] = field(default_factory=list)
    importance: float = 50.0
    magnitude: float = 50.0
    novelty: float = 50.0
    priced_in: float = 50.0
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.event_id = _text(self.event_id, "event_id", required=True)
        self.title = _text(self.title, "title")
        self.event_type = _text(self.event_type, "event_type", required=True).upper()
        self.direction = _text(
            self.direction, "direction", required=True
        ).upper()
        self.horizon = _text(self.horizon, "horizon", required=True).upper()
        self.as_of = ensure_utc(self.as_of, field_name="as_of")
        self.affected_stock_ids = _text_list(self.affected_stock_ids)
        self.affected_theme_ids = _text_list(self.affected_theme_ids)
        self.importance = _score(self.importance, "importance")
        self.magnitude = _score(self.magnitude, "magnitude")
        self.novelty = _score(self.novelty, "novelty")
        self.priced_in = _score(self.priced_in, "priced_in")
        self.confidence = _confidence(self.confidence)
        self.reasons = _text_list(self.reasons)
        self.risks = _text_list(self.risks)
        self.metadata = dict(self.metadata)


@dataclass(slots=True)
class DiscoveryCandidate(Serializable):
    stock_id: str
    discovery_score: float = 0.0
    rank: int = 0
    rank_percentile: float = 1.0
    state: CandidateState | str = CandidateState.DISCOVERED
    market_regime: MarketRegime | str = MarketRegime.SIDEWAYS
    theme_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    score_components: list[ScoreComponent] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model_version: str = "discovery-mvp-v1"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    selected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stock_id = _text(self.stock_id, "stock_id", required=True)
        self.discovery_score = _score(self.discovery_score, "discovery_score")
        self.rank = int(self.rank)
        if self.rank < 0:
            raise ValueError("rank must be non-negative")
        self.rank_percentile = _confidence(
            self.rank_percentile, "rank_percentile"
        )
        self.state = CandidateState(self.state)
        self.market_regime = MarketRegime(self.market_regime)
        self.theme_ids = _text_list(self.theme_ids)
        self.event_ids = _text_list(self.event_ids)
        self.score_components = list(self.score_components)
        self.reasons = _text_list(self.reasons)
        self.risks = _text_list(self.risks)
        self.confidence = _confidence(self.confidence)
        self.model_version = _text(
            self.model_version, "model_version", required=True
        )
        self.created_at = ensure_utc(self.created_at, field_name="created_at")
        self.updated_at = ensure_utc(self.updated_at, field_name="updated_at")
        self.metadata = dict(self.metadata)

    @property
    def scores(self) -> dict[str, float]:
        return {component.name: component.score for component in self.score_components}

    def score_component(self, name: str) -> ScoreComponent | None:
        for component in self.score_components:
            if component.name == name:
                return component
        return None


@dataclass(slots=True)
class DiscoveryResult(Serializable):
    market_regime: MarketRegime | str
    generated_at: datetime
    universe_size: int
    selected_count: int
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    rankings: list[DiscoveryCandidate] = field(default_factory=list)
    stale_candidates: list[DiscoveryCandidate] = field(default_factory=list)
    model_version: str = "discovery-mvp-v1"
    weights: dict[str, float] = field(default_factory=dict)
    theme_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.market_regime = MarketRegime(self.market_regime)
        self.generated_at = ensure_utc(self.generated_at, field_name="generated_at")
        self.universe_size = int(self.universe_size)
        self.selected_count = int(self.selected_count)
        if self.universe_size < 0 or self.selected_count < 0:
            raise ValueError("universe_size and selected_count must be non-negative")
        self.candidates = list(self.candidates)
        self.rankings = list(self.rankings)
        self.stale_candidates = list(self.stale_candidates)
        self.model_version = _text(
            self.model_version, "model_version", required=True
        )
        self.weights = {
            str(name): float(value) for name, value in self.weights.items()
        }
        self.theme_ids = _text_list(self.theme_ids)
        self.event_ids = _text_list(self.event_ids)
        self.metadata = dict(self.metadata)

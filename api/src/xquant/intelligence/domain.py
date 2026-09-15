from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Self, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

T = TypeVar("T", bound="Serializable")


class Serializable:
    """Mixin that provides stable JSON-safe dataclass serialization."""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)  # type: ignore[arg-type]
        return to_json_safe(payload)  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Self:
        if not isinstance(payload, Mapping):
            raise TypeError(f"{cls.__name__} payload must be a mapping")
        field_names = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in field_names})


class SourceType(str, Enum):
    NEWS = "NEWS"
    POLICY = "POLICY"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    MACRO = "MACRO"
    GLOBAL = "GLOBAL"
    MARKET = "MARKET"
    SENTIMENT = "SENTIMENT"
    OTHER = "OTHER"


class InformationStatus(str, Enum):
    COLLECTED = "COLLECTED"
    NORMALIZED = "NORMALIZED"
    DUPLICATE = "DUPLICATE"
    PROCESSED = "PROCESSED"
    REJECTED = "REJECTED"


class EventType(str, Enum):
    POLICY = "POLICY"
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    MONETARY_POLICY = "MONETARY_POLICY"
    REGULATION = "REGULATION"
    INDUSTRY = "INDUSTRY"
    TECHNOLOGY = "TECHNOLOGY"
    COMPANY = "COMPANY"
    EARNINGS = "EARNINGS"
    MERGER = "MERGER"
    ORDER = "ORDER"
    PRODUCT = "PRODUCT"
    PRICE_CHANGE = "PRICE_CHANGE"
    COMMODITY = "COMMODITY"
    MARKET = "MARKET"
    RISK = "RISK"
    BLACK_SWAN = "BLACK_SWAN"
    OTHER = "OTHER"


class ImpactDirection(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"


class ImpactHorizon(str, Enum):
    INTRADAY = "INTRADAY"
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class ThemeState(str, Enum):
    DISCOVERED = "DISCOVERED"
    INCUBATING = "INCUBATING"
    HEATING = "HEATING"
    BREAKOUT = "BREAKOUT"
    CONSENSUS = "CONSENSUS"
    OVERCROWDED = "OVERCROWDED"
    DIVERGENCE = "DIVERGENCE"
    COOLING = "COOLING"
    DEAD = "DEAD"


class ThemeCategory(str, Enum):
    POLICY = "POLICY"
    MACRO = "MACRO"
    INDUSTRY = "INDUSTRY"
    TECHNOLOGY = "TECHNOLOGY"
    COMPANY = "COMPANY"
    RISK = "RISK"
    OTHER = "OTHER"


class BriefType(str, Enum):
    MORNING = "MORNING"
    INTRADAY = "INTRADAY"
    CLOSING = "CLOSING"
    WEEKLY = "WEEKLY"


class NotificationSeverity(str, Enum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    IMPORTANT = "IMPORTANT"
    CRITICAL = "CRITICAL"


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(
    value: datetime | date | str | None,
    *,
    field_name: str = "datetime",
    default: datetime | None = None,
) -> datetime | None:
    """Parse a datetime and normalize it to an aware UTC datetime."""

    if value is None:
        return default
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO datetime") from exc
    else:
        raise TypeError(f"{field_name} must be a datetime or ISO datetime string")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_iso(value: datetime | date | str | None) -> str | None:
    parsed = ensure_utc(value)
    return parsed.isoformat() if parsed is not None else None


def to_json_safe(value: Any) -> Any:
    """Convert values to a structure accepted by ``json.dumps``."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return utc_iso(value)
    if isinstance(value, (UUID, Path)):
        return str(value)
    if is_dataclass(value) and hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [to_json_safe(item) for item in sorted(value, key=str)]
    return str(value)


def compute_content_hash(title: str, content: str) -> str:
    normalized = f"{title.strip()}\n{content.strip()}".encode()
    return hashlib.sha256(normalized).hexdigest()


def information_id(content_hash: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://xquant.local/intelligence/information/{content_hash}")


@dataclass(slots=True)
class RawInformation(Serializable):
    id: UUID = field(default_factory=uuid4)
    source: str = ""
    source_type: SourceType | str = SourceType.NEWS
    url: str | None = None
    title: str = ""
    content: str = ""
    author: str | None = None
    event_time: datetime | None = None
    publish_time: datetime = field(default_factory=utc_now)
    fetch_time: datetime = field(default_factory=utc_now)
    process_time: datetime | None = None
    available_time: datetime | None = None
    language: str = "zh-CN"
    raw_payload: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    status: InformationStatus | str = InformationStatus.COLLECTED

    def __post_init__(self) -> None:
        self.id = self.id if isinstance(self.id, UUID) else UUID(str(self.id))
        self.source = str(self.source).strip()
        self.source_type = SourceType(self.source_type)
        self.title = str(self.title).strip()
        self.content = str(self.content).strip()
        self.url = str(self.url).strip() if self.url is not None else None
        self.author = str(self.author).strip() if self.author is not None else None
        self.language = str(self.language or "zh-CN").strip()
        self.raw_payload = dict(self.raw_payload or {})
        self.content_hash = str(self.content_hash or "").strip()
        self.status = InformationStatus(self.status)

        self.publish_time = ensure_utc(self.publish_time, field_name="publish_time") or utc_now()
        self.fetch_time = ensure_utc(self.fetch_time, field_name="fetch_time") or utc_now()
        self.event_time = ensure_utc(self.event_time, field_name="event_time")
        process_time = ensure_utc(self.process_time, field_name="process_time")
        available_time = ensure_utc(self.available_time, field_name="available_time")
        if process_time is not None:
            process_time = max(process_time, self.fetch_time)
        if available_time is not None:
            if process_time is None:
                process_time = max(available_time, self.fetch_time)
            else:
                available_time = max(available_time, process_time)
        self.process_time = process_time
        self.available_time = available_time
        if self.event_time is not None and self.process_time is not None:
            self.event_time = min(self.event_time, self.process_time)
        if self.publish_time is not None and self.process_time is not None:
            self.publish_time = min(self.publish_time, self.process_time)


@dataclass(slots=True)
class EventImpact(Serializable):
    direction: ImpactDirection | str = ImpactDirection.NEUTRAL
    magnitude: float = 0.0
    confidence: float = 0.0
    novelty: float = 0.0
    priced_in: float = 0.0
    horizon: ImpactHorizon | str = ImpactHorizon.SHORT
    affected_themes: list[str] = field(default_factory=list)
    affected_industries: list[str] = field(default_factory=list)
    affected_products: list[str] = field(default_factory=list)
    affected_companies: list[str] = field(default_factory=list)
    affected_stocks: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    capital_score: float = 0.0

    def __post_init__(self) -> None:
        self.direction = ImpactDirection(self.direction)
        self.horizon = ImpactHorizon(self.horizon)
        self.magnitude = _bounded(self.magnitude, 0.0, 100.0)
        self.confidence = _bounded(self.confidence, 0.0, 1.0)
        self.novelty = _bounded(self.novelty, 0.0, 1.0)
        self.priced_in = _bounded(self.priced_in, 0.0, 1.0)
        self.capital_score = _bounded(self.capital_score, 0.0, 100.0)
        self.affected_themes = _unique_strings(self.affected_themes)
        self.affected_industries = _unique_strings(self.affected_industries)
        self.affected_products = _unique_strings(self.affected_products)
        self.affected_companies = _unique_strings(self.affected_companies)
        self.affected_stocks = _unique_strings(self.affected_stocks)
        self.risks = _unique_strings(self.risks)


@dataclass(slots=True)
class MarketEvent(Serializable):
    id: UUID = field(default_factory=uuid4)
    event_type: EventType | str = EventType.OTHER
    title: str = ""
    summary: str = ""
    country: str | None = None
    importance: float = 0.0
    sentiment: float = 0.0
    confidence: float = 0.0
    novelty: float = 0.0
    impact_direction: ImpactDirection | str = ImpactDirection.NEUTRAL
    impact_horizon: ImpactHorizon | str = ImpactHorizon.SHORT
    event_time: datetime = field(default_factory=utc_now)
    first_publish_time: datetime = field(default_factory=utc_now)
    last_update_time: datetime = field(default_factory=utc_now)
    heat_score: float = 0.0
    source_count: int = 0
    status: str = "ACTIVE"
    information_ids: list[UUID] = field(default_factory=list)
    impact: EventImpact = field(default_factory=EventImpact)

    def __post_init__(self) -> None:
        self.id = self.id if isinstance(self.id, UUID) else UUID(str(self.id))
        self.event_type = EventType(self.event_type)
        self.impact_direction = ImpactDirection(self.impact_direction)
        self.impact_horizon = ImpactHorizon(self.impact_horizon)
        self.title = str(self.title).strip()
        self.summary = str(self.summary).strip()
        self.country = str(self.country).strip() if self.country is not None else None
        self.importance = _bounded(self.importance, 0.0, 100.0)
        self.sentiment = _bounded(self.sentiment, -1.0, 1.0)
        self.confidence = _bounded(self.confidence, 0.0, 1.0)
        self.novelty = _bounded(self.novelty, 0.0, 1.0)
        self.heat_score = _bounded(self.heat_score, 0.0, 100.0)
        self.source_count = max(0, int(self.source_count))
        self.event_time = (
            ensure_utc(self.event_time, field_name="event_time") or utc_now()
        )
        self.first_publish_time = (
            ensure_utc(self.first_publish_time, field_name="first_publish_time")
            or self.event_time
        )
        self.last_update_time = (
            ensure_utc(self.last_update_time, field_name="last_update_time")
            or self.first_publish_time
        )
        self.last_update_time = max(self.last_update_time, self.first_publish_time)
        self.information_ids = _unique_uuids(self.information_ids)
        if not isinstance(self.impact, EventImpact):
            self.impact = EventImpact(**dict(self.impact))


@dataclass(slots=True)
class Theme(Serializable):
    id: UUID = field(default_factory=uuid4)
    code: str = ""
    name: str = ""
    description: str = ""
    state: ThemeState | str = ThemeState.DISCOVERED
    category: ThemeCategory | str = ThemeCategory.OTHER
    current_heat_score: float = 0.0
    forward_heat_score: float = 0.0
    crowding_score: float = 0.0
    sentiment_score: float = 50.0
    policy_score: float = 0.0
    capital_score: float = 0.0
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    event_ids: list[UUID] = field(default_factory=list)
    information_ids: list[UUID] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    model_version: str = "rules-v1"

    @property
    def current_heat(self) -> float:
        return self.current_heat_score

    @property
    def forward_heat(self) -> float:
        return self.forward_heat_score

    @property
    def crowding(self) -> float:
        return self.crowding_score

    @property
    def sentiment(self) -> float:
        return self.sentiment_score

    @property
    def policy(self) -> float:
        return self.policy_score

    @property
    def capital(self) -> float:
        return self.capital_score

    def __post_init__(self) -> None:
        self.id = self.id if isinstance(self.id, UUID) else UUID(str(self.id))
        self.state = ThemeState(self.state)
        self.category = ThemeCategory(self.category)
        self.code = str(self.code).strip()
        self.name = str(self.name).strip()
        self.description = str(self.description).strip()
        self.current_heat_score = _bounded(self.current_heat_score, 0.0, 100.0)
        self.forward_heat_score = _bounded(self.forward_heat_score, 0.0, 100.0)
        self.crowding_score = _bounded(self.crowding_score, 0.0, 100.0)
        self.sentiment_score = _bounded(self.sentiment_score, 0.0, 100.0)
        self.policy_score = _bounded(self.policy_score, 0.0, 100.0)
        self.capital_score = _bounded(self.capital_score, 0.0, 100.0)
        self.confidence = _bounded(self.confidence, 0.0, 1.0)
        self.reasons = _unique_strings(self.reasons)
        self.event_ids = _unique_uuids(self.event_ids)
        self.information_ids = _unique_uuids(self.information_ids)
        self.created_at = ensure_utc(self.created_at, field_name="created_at") or utc_now()
        self.updated_at = ensure_utc(self.updated_at, field_name="updated_at") or utc_now()
        self.updated_at = max(self.updated_at, self.created_at)

    def to_dict(self) -> dict[str, Any]:
        payload = Serializable.to_dict(self)
        payload.update(
            {
                "current_heat": self.current_heat_score,
                "forward_heat": self.forward_heat_score,
                "crowding": self.crowding_score,
                "sentiment": self.sentiment_score,
                "policy": self.policy_score,
                "capital": self.capital_score,
            }
        )
        return payload


@dataclass(slots=True)
class InformationBrief(Serializable):
    id: UUID = field(default_factory=uuid4)
    brief_type: BriefType | str = BriefType.MORNING
    title: str = ""
    summary: str = ""
    content: dict[str, Any] = field(default_factory=dict)
    markdown: str = ""
    generated_at: datetime = field(default_factory=utc_now)
    available_time: datetime = field(default_factory=utc_now)
    event_ids: list[UUID] = field(default_factory=list)
    theme_ids: list[UUID] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = self.id if isinstance(self.id, UUID) else UUID(str(self.id))
        self.brief_type = BriefType(self.brief_type)
        self.title = str(self.title).strip()
        self.summary = str(self.summary).strip()
        self.content = dict(self.content or {})
        self.markdown = str(self.markdown or "")
        self.generated_at = (
            ensure_utc(self.generated_at, field_name="generated_at") or utc_now()
        )
        self.available_time = (
            ensure_utc(self.available_time, field_name="available_time")
            or self.generated_at
        )
        self.available_time = max(self.available_time, self.generated_at)
        self.event_ids = _unique_uuids(self.event_ids)
        self.theme_ids = _unique_uuids(self.theme_ids)
        self.metadata = dict(self.metadata or {})


@dataclass(slots=True)
class NotificationEvent(Serializable):
    id: UUID = field(default_factory=uuid4)
    type: str = "INTELLIGENCE"
    severity: NotificationSeverity | str = NotificationSeverity.INFO
    title: str = ""
    content: str = ""
    user_id: str | None = None
    stock_ids: list[str] = field(default_factory=list)
    theme_ids: list[UUID] = field(default_factory=list)
    event_ids: list[UUID] = field(default_factory=list)
    channels: list[str] = field(default_factory=lambda: ["web"])
    dedup_key: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.id = self.id if isinstance(self.id, UUID) else UUID(str(self.id))
        self.severity = NotificationSeverity(self.severity)
        self.type = str(self.type).strip() or "INTELLIGENCE"
        self.title = str(self.title).strip()
        self.content = str(self.content).strip()
        self.user_id = str(self.user_id).strip() if self.user_id is not None else None
        self.stock_ids = _unique_strings(self.stock_ids)
        self.theme_ids = _unique_uuids(self.theme_ids)
        self.event_ids = _unique_uuids(self.event_ids)
        self.channels = _unique_strings(self.channels)
        self.created_at = (
            ensure_utc(self.created_at, field_name="created_at") or utc_now()
        )
        if not self.dedup_key:
            self.dedup_key = self.id.hex


def _bounded(value: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = lower
    if not math.isfinite(parsed):
        parsed = lower
    return max(lower, min(upper, parsed))


def _unique_strings(values: Sequence[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _unique_uuids(values: Sequence[UUID | str] | None) -> list[UUID]:
    seen: set[UUID] = set()
    result: list[UUID] = []
    for value in values or []:
        item = value if isinstance(value, UUID) else UUID(str(value))
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


__all__ = [
    "BriefType",
    "EventImpact",
    "EventType",
    "ImpactDirection",
    "ImpactHorizon",
    "InformationBrief",
    "InformationStatus",
    "MarketEvent",
    "NotificationEvent",
    "NotificationSeverity",
    "RawInformation",
    "Serializable",
    "SourceType",
    "Theme",
    "ThemeCategory",
    "ThemeState",
    "compute_content_hash",
    "ensure_utc",
    "information_id",
    "to_json_safe",
    "utc_iso",
    "utc_now",
]

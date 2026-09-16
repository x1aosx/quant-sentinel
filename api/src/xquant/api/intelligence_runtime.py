from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from xquant.discovery import (
    DiscoveryCandidate,
    DiscoveryService,
    EventSnapshot,
    MarketRegime,
    ScoreComponent,
    StockSnapshot,
    ThemeSnapshot,
)
from xquant.intelligence import (
    AnnouncementProvider,
    IntelligenceRepository,
    IntelligenceService,
    NewsProvider,
    PolicyProvider,
    PostgresIntelligenceRepository,
    RssInformationSource,
    SourceType,
    SqliteIntelligenceRepository,
)
from xquant.storage import StorageSettings
from xquant.system_config import SystemConfigStore

_HOT_THEATRES = {"HEATING", "BREAKOUT", "CONSENSUS", "OVERCROWDED"}
_EMERGING_STATES = {"DISCOVERED", "INCUBATING", "BREAKOUT"}


@dataclass(slots=True)
class IntelligenceRuntime:
    repository: IntelligenceRepository
    service: IntelligenceService
    api_service: ApplicationIntelligenceService
    discovery_service: ApplicationDiscoveryService


class ApplicationIntelligenceService:
    """API-facing projection over the intelligence domain service."""

    def __init__(
        self,
        service: IntelligenceService,
        repository: IntelligenceRepository,
        source_factory: Callable[[], list[Any]],
    ) -> None:
        self._service = service
        self._repository = repository
        self._source_factory = source_factory
        self._refresh_sources()

    async def run(self, *, process_only: bool = False) -> dict[str, Any]:
        self._refresh_sources()
        if process_only:
            result = await self._service.process()
            collected = 0
        else:
            result = await self._service.run()
            collected = int(result.get("collected") or 0)
        return {
            "collected": collected,
            "deduplicated": int(result.get("duplicates") or 0),
            "event_count": int(result.get("events") or 0),
            "theme_count": int(result.get("themes") or 0),
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def overview(self) -> dict[str, Any]:
        self._refresh_sources()
        counts = self._repository.counts()
        events = self.list_events(limit=20)
        themes = self.list_themes(limit=20, category="all")
        brief = self.get_morning_brief()
        return {
            "information_count": int(counts.get("information") or 0),
            "event_count": int(counts.get("events") or 0),
            "theme_count": int(counts.get("themes") or 0),
            "source_count": len(self._service.sources),
            "generated_at": datetime.now(UTC).isoformat(),
            "hot_events": [event.to_dict() for event in events],
            "themes": [theme.to_dict() for theme in themes],
            "brief": brief.to_dict() if brief is not None else None,
        }

    def list_events(self, *, limit: int = 50, hot_only: bool = False) -> list[Any]:
        items = self._service.list_events(limit=max(limit * 3, limit))
        if hot_only:
            items = [item for item in items if item.heat_score >= 60]
        return items[:limit]

    def list_information(self, *, limit: int = 50) -> list[Any]:
        return self._repository.list_information(limit=limit)

    def list_sources(self) -> list[dict[str, Any]]:
        self._refresh_sources()
        result: list[dict[str, Any]] = []
        for index, source in enumerate(self._service.sources, start=1):
            feed = getattr(source, "source", source)
            source_type = getattr(
                source,
                "source_type",
                getattr(source, "default_source_type", SourceType.NEWS),
            )
            result.append(
                {
                    "id": str(
                        getattr(feed, "source", "") or f"source-{index}"
                    ),
                    "source": str(getattr(feed, "source", "") or ""),
                    "source_type": getattr(source_type, "value", str(source_type)),
                    "url": str(getattr(feed, "feed_url", "") or ""),
                }
            )
        return result

    def _refresh_sources(self) -> None:
        self._service.sources = list(self._source_factory())

    def list_themes(self, *, limit: int = 50, category: str = "all") -> list[Any]:
        items = self._service.list_themes(limit=5000)
        normalized = str(category or "all").lower()
        if normalized == "hot":
            items = [
                item
                for item in items
                if item.current_heat_score >= 60
                or item.state.value in _HOT_THEATRES
            ]
        elif normalized == "emerging":
            items = [
                item
                for item in items
                if item.forward_heat_score > item.current_heat_score + 5
                or item.state.value in _EMERGING_STATES
            ]
        elif normalized == "overcrowded":
            items = [
                item
                for item in items
                if item.crowding_score >= 70
                or item.state.value == "OVERCROWDED"
            ]
        elif normalized not in {"", "all"}:
            raise ValueError("主题分类仅支持 hot/emerging/overcrowded/all")
        return items[:limit]

    def get_morning_brief(self) -> Any | None:
        return self._service.get_morning_brief()


class ApplicationDiscoveryService:
    """API-facing discovery service with a durable candidate projection."""

    def __init__(
        self,
        service: DiscoveryService,
        data_source: DatabaseDiscoveryDataSource,
        store: DiscoveryCandidateStore,
    ) -> None:
        self._service = service
        self._data_source = data_source
        self._store = store

    def list_candidates(self, *, state: str = "all", limit: int = 100) -> dict[str, Any]:
        candidates = self._store.list_candidates()
        normalized = str(state or "all").upper()
        if normalized != "ALL":
            candidates = [
                candidate
                for candidate in candidates
                if candidate.state.value == normalized
            ]
        candidates = sorted(candidates, key=lambda item: (item.rank or 10**9, item.stock_id))
        trade_date = max(
            (candidate.updated_at.date().isoformat() for candidate in candidates),
            default=datetime.now(UTC).date().isoformat(),
        )
        items = [_candidate_payload(candidate) for candidate in candidates[:limit]]
        return {"trade_date": trade_date, "items": items, "count": len(items)}

    def refresh(self) -> dict[str, Any]:
        snapshots = self._data_source.list_stock_snapshots()
        regime = _infer_market_regime(snapshots)
        self._service.run(regime)
        return self.list_candidates(state="all", limit=1000)


class DatabaseDiscoveryDataSource:
    """Build discovery snapshots from persisted datasets and intelligence."""

    def __init__(
        self,
        database: Any,
        intelligence_repository: IntelligenceRepository,
    ) -> None:
        self.database = database
        self.intelligence_repository = intelligence_repository
        self._stock_cache: list[StockSnapshot] | None = None

    def list_stock_snapshots(self) -> Sequence[StockSnapshot]:
        themes = self.list_theme_snapshots()
        events = self.list_event_snapshots()
        theme_by_name = {theme.name: theme for theme in themes}
        stocks: list[StockSnapshot] = []
        raw_liquidity: list[float] = []

        datasets = list(self.database.list_datasets())
        for summary in datasets:
            symbol = str(summary.get("symbol") or "").strip().upper()
            if not symbol or symbol.startswith("^") or "=" in symbol:
                continue
            try:
                bars = list(self.database.get_dataset(str(summary["id"])).get("bars") or [])
            except (KeyError, TypeError, ValueError):
                continue
            if len(bars) < 20:
                continue
            closes = [_float(bar.get("close")) for bar in bars[-60:]]
            volumes = [_float(bar.get("volume")) for bar in bars[-20:]]
            closes = [value for value in closes if value is not None and value > 0]
            volumes = [value for value in volumes if value is not None and value >= 0]
            if len(closes) < 20:
                continue
            change_20d = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else 0.0
            returns = [
                closes[index] / closes[index - 1] - 1
                for index in range(1, len(closes))
                if closes[index - 1] > 0
            ]
            volatility = _standard_deviation(returns) * math.sqrt(252)
            average_volume = mean(volumes) if volumes else 0.0
            turnover_proxy = average_volume * closes[-1]
            raw_liquidity.append(turnover_proxy)

            linked_themes: list[Any] = []
            linked_events: list[Any] = []
            for event in events:
                if symbol not in event.affected_stock_ids and symbol.split(".")[0] not in event.affected_stock_ids:
                    continue
                linked_events.append(event)
                for theme_id in event.affected_theme_ids:
                    theme = next((item for item in themes if item.theme_id == theme_id), None)
                    if theme is not None and theme not in linked_themes:
                        linked_themes.append(theme)
            for event in self.intelligence_repository.list_events(limit=5000):
                affected = set(event.impact.affected_stocks)
                if symbol not in affected and symbol.split(".")[0] not in affected:
                    continue
                for theme_name in event.impact.affected_themes:
                    theme = theme_by_name.get(theme_name)
                    if theme is not None and theme not in linked_themes:
                        linked_themes.append(theme)

            stocks.append(
                _stock_snapshot(
                    summary=summary,
                    closes=closes,
                    change_20d=change_20d,
                    volatility=volatility,
                    turnover_proxy=turnover_proxy,
                    themes=linked_themes,
                    events=linked_events,
                )
            )

        for stock in stocks:
            turnover = float(stock.metadata.get("turnover_proxy") or 0.0)
            stock.scores["liquidity"] = _percentile_score(turnover, raw_liquidity)
        self._stock_cache = stocks
        return stocks

    def list_theme_snapshots(self) -> Sequence[ThemeSnapshot]:
        return [
            ThemeSnapshot(
                theme_id=str(theme.id),
                name=theme.name,
                state=theme.state.value,
                as_of=theme.updated_at,
                current_heat_score=theme.current_heat_score,
                forward_heat_score=theme.forward_heat_score,
                crowding_score=theme.crowding_score,
                sentiment_score=theme.sentiment_score,
                policy_score=theme.policy_score,
                capital_score=theme.capital_score,
                attention_momentum_score=min(
                    100.0,
                    max(0.0, theme.forward_heat_score - theme.current_heat_score + 50.0),
                ),
                confidence=theme.confidence,
                reasons=list(theme.reasons),
            )
            for theme in self.intelligence_repository.list_themes(limit=5000)
        ]

    def list_event_snapshots(self) -> Sequence[EventSnapshot]:
        return [
            EventSnapshot(
                event_id=str(event.id),
                title=event.title,
                event_type=event.event_type.value,
                direction=event.impact_direction.value,
                horizon=event.impact_horizon.value,
                as_of=event.last_update_time,
                affected_stock_ids=list(event.impact.affected_stocks),
                affected_theme_ids=[
                    str(theme.id)
                    for theme in self.intelligence_repository.list_themes(limit=5000)
                    if theme.name in event.impact.affected_themes
                ],
                importance=event.importance,
                magnitude=event.impact.magnitude,
                novelty=event.novelty * 100,
                priced_in=event.impact.priced_in * 100,
                confidence=event.confidence,
                reasons=[event.summary] if event.summary else [],
            )
            for event in self.intelligence_repository.list_events(limit=5000)
        ]


class DiscoveryCandidateStore:
    """SQLite/PostgreSQL persistence for the current candidate pool."""

    def __init__(self, database: Any, settings: StorageSettings) -> None:
        self.database = database
        self.settings = settings
        self._sqlite = settings.storage_backend == "legacy_sqlite"
        self.initialize()

    def initialize(self) -> None:
        if self._sqlite:
            path = Path(self.database.path)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS discovery_candidates (
                        stock_id TEXT PRIMARY KEY,
                        trade_date TEXT NOT NULL,
                        discovery_score REAL NOT NULL,
                        rank INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        market_regime TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        model_version TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_discovery_candidates_state_rank
                    ON discovery_candidates (state, rank)
                    """
                )
                connection.commit()
            finally:
                connection.close()
            return

        self.database.postgres.execute(
            """
            CREATE SCHEMA IF NOT EXISTS discovery;
            CREATE TABLE IF NOT EXISTS discovery.candidate (
                stock_id TEXT PRIMARY KEY,
                trade_date DATE NOT NULL,
                discovery_score DOUBLE PRECISION NOT NULL,
                rank INTEGER NOT NULL,
                state TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                model_version TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_candidate_state_rank
                ON discovery.candidate (state, rank);
            """
        )

    def list_candidates(self) -> list[DiscoveryCandidate]:
        if self._sqlite:
            connection = sqlite3.connect(Path(self.database.path))
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    "SELECT payload FROM discovery_candidates ORDER BY rank, stock_id"
                ).fetchall()
            finally:
                connection.close()
            return [_candidate_from_payload(row["payload"]) for row in rows]

        rows = self.database.postgres.query(
            "SELECT payload FROM discovery.candidate ORDER BY rank, stock_id"
        )
        return [
            _candidate_from_payload(
                row["payload"]
                if isinstance(row["payload"], str)
                else json.dumps(row["payload"])
            )
            for row in rows
        ]

    def save_candidates(self, candidates: Sequence[DiscoveryCandidate]) -> int:
        count = 0
        for candidate in candidates:
            payload = json.dumps(candidate.to_dict(), ensure_ascii=False)
            values = {
                "stock_id": candidate.stock_id,
                "trade_date": candidate.updated_at.date().isoformat(),
                "discovery_score": candidate.discovery_score,
                "rank": candidate.rank,
                "state": candidate.state.value,
                "market_regime": candidate.market_regime.value,
                "confidence": candidate.confidence,
                "model_version": candidate.model_version,
                "updated_at": candidate.updated_at.isoformat(),
                "payload": payload,
            }
            if self._sqlite:
                connection = sqlite3.connect(Path(self.database.path))
                try:
                    connection.execute(
                        """
                        INSERT INTO discovery_candidates
                            (stock_id, trade_date, discovery_score, rank, state,
                             market_regime, confidence, model_version, updated_at, payload)
                        VALUES
                            (:stock_id, :trade_date, :discovery_score, :rank, :state,
                             :market_regime, :confidence, :model_version, :updated_at, :payload)
                        ON CONFLICT(stock_id) DO UPDATE SET
                            trade_date = excluded.trade_date,
                            discovery_score = excluded.discovery_score,
                            rank = excluded.rank,
                            state = excluded.state,
                            market_regime = excluded.market_regime,
                            confidence = excluded.confidence,
                            model_version = excluded.model_version,
                            updated_at = excluded.updated_at,
                            payload = excluded.payload
                        """,
                        values,
                    )
                    connection.commit()
                finally:
                    connection.close()
            else:
                self.database.postgres.execute(
                    """
                    INSERT INTO discovery.candidate
                        (stock_id, trade_date, discovery_score, rank, state,
                         market_regime, confidence, model_version, updated_at, payload)
                    VALUES
                        (:stock_id, CAST(:trade_date AS date), :discovery_score, :rank,
                         :state, :market_regime, :confidence, :model_version,
                         CAST(:updated_at AS timestamptz), CAST(:payload AS jsonb))
                    ON CONFLICT (stock_id) DO UPDATE SET
                        trade_date = excluded.trade_date,
                        discovery_score = excluded.discovery_score,
                        rank = excluded.rank,
                        state = excluded.state,
                        market_regime = excluded.market_regime,
                        confidence = excluded.confidence,
                        model_version = excluded.model_version,
                        updated_at = excluded.updated_at,
                        payload = excluded.payload
                    """,
                    values,
                )
            count += 1
        return count


def build_intelligence_runtime(
    database: Any,
    settings: StorageSettings,
    system_config: SystemConfigStore,
) -> IntelligenceRuntime:
    repository: IntelligenceRepository
    if settings.storage_backend == "legacy_sqlite":
        repository = SqliteIntelligenceRepository(database)
    else:
        repository = PostgresIntelligenceRepository(database)
    config = system_config.settings.intelligence

    def source_factory() -> list[Any]:
        return _build_sources(system_config.settings.intelligence)

    sources = source_factory()
    service = IntelligenceService(
        repository,
        sources,
        lookback_hours=config.lookback_hours,
    )
    data_source = DatabaseDiscoveryDataSource(database, repository)
    candidate_store = DiscoveryCandidateStore(database, settings)
    discovery = DiscoveryService(
        data_source,
        candidate_store,
        top_percentile=0.30,
    )
    return IntelligenceRuntime(
        repository=repository,
        service=service,
        api_service=ApplicationIntelligenceService(
            service,
            repository,
            source_factory,
        ),
        discovery_service=ApplicationDiscoveryService(
            discovery,
            data_source,
            candidate_store,
        ),
    )


def _build_sources(config: Any) -> list[Any]:
    sources: list[Any] = []
    for index, feed in enumerate(config.feeds, start=1):
        if not getattr(feed, "enabled", True):
            continue
        source_type = SourceType(str(feed.source_type).upper())
        source = RssInformationSource(
            str(feed.url),
            source=str(feed.source or feed.id or f"feed-{index}"),
            source_type=source_type,
            language=str(feed.language or "zh-CN"),
        )
        if source_type == SourceType.POLICY:
            sources.append(PolicyProvider(source))
        elif source_type == SourceType.ANNOUNCEMENT:
            sources.append(AnnouncementProvider(source))
        else:
            sources.append(NewsProvider(source))
    return sources


def _stock_snapshot(
    *,
    summary: dict[str, Any],
    closes: Sequence[float],
    change_20d: float,
    volatility: float,
    turnover_proxy: float,
    themes: Sequence[ThemeSnapshot],
    events: Sequence[EventSnapshot],
) -> StockSnapshot:
    symbol = str(summary.get("symbol") or "").upper()
    theme_score = _average([theme.current_heat_score for theme in themes], 45.0)
    forward_score = _average([theme.forward_heat_score for theme in themes], 45.0)
    policy_score = _average([theme.policy_score for theme in themes], 45.0)
    capital_score = _average([theme.capital_score for theme in themes], 45.0)
    sentiment_score = _average([theme.sentiment_score for theme in themes], 50.0)
    crowding_score = _average([theme.crowding_score for theme in themes], 45.0)
    attention_score = min(
        100.0,
        max(0.0, 50.0 + (forward_score - theme_score) * 1.4),
    )
    event_score = _event_score(events)
    price_action = _clamp(50.0 + change_20d * 250.0, 0.0, 100.0)
    risk = _clamp(volatility * 120.0, 0.0, 100.0)
    reasons = [
        f"20 日涨跌幅 {change_20d:.1%}",
        f"年化波动率 {volatility:.1%}",
        f"关联主题 {len(themes)} 个",
    ]
    risks = [f"主题拥挤度 {crowding_score:.0f}"] if crowding_score >= 70 else []
    return StockSnapshot(
        stock_id=symbol,
        name=str(summary.get("title") or symbol),
        theme_ids=[theme.theme_id for theme in themes],
        event_ids=[event.event_id for event in events],
        scores={
            "market_fit": 55.0,
            "theme": theme_score,
            "forward_theme": forward_score,
            "policy": policy_score,
            "global_event": event_score,
            "sentiment": sentiment_score,
            "attention_momentum": attention_score,
            "capital": capital_score,
            "price_action": price_action,
            "alpha": price_action,
            "fundamental": 50.0,
            "liquidity": 50.0,
            "crowding": crowding_score,
            "risk": risk,
        },
        score_reasons={
            "theme": [f"关联主题当前热度 {theme_score:.0f}"],
            "forward_theme": [f"关联主题未来热度 {forward_score:.0f}"],
            "price_action": [f"20 日动量得分 {price_action:.0f}"],
            "risk": [f"波动风险得分 {risk:.0f}"],
        },
        score_risks={"risk": risks or ["波动风险未超阈值"]},
        reasons=reasons,
        risks=risks,
        confidence=0.65 if events or themes else 0.45,
        metadata={
            "stock_name": str(summary.get("title") or symbol),
            "stock_code": symbol.split(".")[0],
            "symbol": symbol,
            "dataset_id": str(summary.get("id") or ""),
            "timeframe": str(summary.get("timeframe") or ""),
            "price": closes[-1],
            "change_20d": change_20d,
            "volatility": volatility,
            "turnover_proxy": turnover_proxy,
        },
    )


def _candidate_payload(candidate: DiscoveryCandidate) -> dict[str, Any]:
    payload = candidate.to_dict()
    metadata = candidate.metadata or {}
    components = [
        {
            "key": component.name,
            "label": component.name,
            "score": component.score,
            "weight": component.weight,
            "contribution": component.weighted_value,
            "reasons": component.reasons,
            "risks": component.risks,
        }
        for component in candidate.score_components
    ]
    payload.update(
        {
            "stock_code": metadata.get("stock_code"),
            "code": metadata.get("stock_code"),
            "symbol": metadata.get("symbol"),
            "stock_name": metadata.get("stock_name"),
            "name": metadata.get("stock_name"),
            "theme_names": [
                str(theme_id)
                for theme_id in candidate.theme_ids
            ],
            "scores": {component.name: component.score for component in candidate.score_components},
            "score_contributions": components,
            "forward_heat_score": _component_score(candidate, "forward_theme"),
            "crowding_score": _component_score(candidate, "crowding"),
            "risk_score": _component_score(candidate, "risk"),
        }
    )
    return payload


def _candidate_from_payload(payload: str) -> DiscoveryCandidate:
    raw = json.loads(payload)
    raw["score_components"] = [
        ScoreComponent(**component)
        for component in raw.get("score_components") or []
    ]
    return DiscoveryCandidate(**raw)


def _component_score(candidate: DiscoveryCandidate, name: str) -> float | None:
    component = candidate.score_component(name)
    return component.score if component is not None else None


def _event_score(events: Sequence[EventSnapshot]) -> float:
    if not events:
        return 50.0
    values = []
    for event in events:
        direction = event.direction.upper()
        strength = event.importance * event.magnitude / 100.0
        if direction == "POSITIVE":
            values.append(50.0 + strength / 2.0)
        elif direction == "NEGATIVE":
            values.append(50.0 - strength / 2.0)
        else:
            values.append(50.0)
    return _average(values, 50.0)


def _infer_market_regime(stocks: Sequence[StockSnapshot]) -> MarketRegime:
    if not stocks:
        return MarketRegime.SIDEWAYS
    changes = [float(stock.metadata.get("change_20d") or 0.0) for stock in stocks]
    volatility = [float(stock.metadata.get("volatility") or 0.0) for stock in stocks]
    average_change = mean(changes)
    if average_change >= 0.04:
        return MarketRegime.BULL
    if average_change <= -0.04:
        return MarketRegime.BEAR
    if mean(volatility) >= 0.55:
        return MarketRegime.HIGH_VOLATILITY
    return MarketRegime.SIDEWAYS


def _percentile_score(value: float, values: Sequence[float]) -> float:
    finite = [item for item in values if math.isfinite(item)]
    if not finite:
        return 50.0
    lower = sum(1 for item in finite if item <= value)
    return _clamp(lower / len(finite) * 100.0, 0.0, 100.0)


def _standard_deviation(values: Sequence[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    if len(finite) < 2:
        return 0.0
    average = mean(finite)
    variance = sum((value - average) ** 2 for value in finite) / (len(finite) - 1)
    return math.sqrt(max(0.0, variance))


def _average(values: Sequence[float], default: float = 0.0) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return mean(finite) if finite else default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None

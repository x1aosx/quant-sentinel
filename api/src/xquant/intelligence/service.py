from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .domain import (
    BriefType,
    InformationBrief,
    InformationStatus,
    MarketEvent,
    NotificationEvent,
    NotificationSeverity,
    RawInformation,
    Theme,
    ensure_utc,
    utc_iso,
    utc_now,
)
from .pipeline import IntelligencePipeline
from .providers import InformationSource
from .repository import IntelligenceRepository


class IntelligenceService:
    def __init__(
        self,
        repository: IntelligenceRepository,
        sources: Sequence[InformationSource],
        *,
        pipeline: IntelligencePipeline | None = None,
        lookback_hours: int = 24,
    ) -> None:
        self.repository = repository
        self.sources = list(sources)
        self.pipeline = pipeline or IntelligencePipeline()
        self.lookback_hours = max(1, int(lookback_hours))

    async def collect(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cursor: str | None = None,
    ) -> list[RawInformation]:
        end = ensure_utc(end_time, field_name="end_time") or utc_now()
        start = ensure_utc(start_time, field_name="start_time") or (
            end - timedelta(hours=self.lookback_hours)
        )
        if start > end:
            raise ValueError("start_time cannot be later than end_time")

        batches = await asyncio.gather(
            *(source.fetch(start, end, cursor) for source in self.sources),
            return_exceptions=False,
        )
        items = [item for batch in batches for item in batch]
        self.repository.save_information(items)
        return items

    async def process(
        self,
        information: Sequence[RawInformation] | None = None,
        *,
        now: datetime | None = None,
        generate_brief: bool = True,
    ) -> dict[str, Any]:
        current = ensure_utc(now, field_name="now") or utc_now()
        if information is None:
            information = self.repository.list_information(
                limit=5000,
                status=InformationStatus.COLLECTED,
                available_before=current,
            )
        normalized = self.pipeline.normalize(information, now=current)
        self.repository.save_information(normalized)

        deduplicated = self.pipeline.deduplicate(normalized)
        duplicate_items = [
            replace(item, status=InformationStatus.DUPLICATE)
            for item in deduplicated.duplicates
        ]
        processed_items = [
            replace(item, status=InformationStatus.PROCESSED)
            for item in deduplicated.unique
        ]
        self.repository.save_information(duplicate_items)
        self.repository.save_information(processed_items)

        events = self.pipeline.cluster_and_analyze(processed_items)
        self.repository.save_events(events)
        all_events = self.repository.list_events(limit=1000, available_before=current)
        themes = self.pipeline.update_themes(
            all_events,
            existing=self.repository.list_themes(limit=5000),
            now=current,
        )
        self.repository.save_themes(themes)
        brief = self._generate_morning_brief(all_events, themes, now=current)
        if generate_brief:
            self.repository.save_brief(brief)
        notifications = self._event_notifications(events, now=current)
        self.repository.save_notifications(notifications)
        return {
            "normalized": len(normalized),
            "unique": len(processed_items),
            "duplicates": len(duplicate_items),
            "events": len(events),
            "themes": len(themes),
            "brief_id": str(brief.id),
            "notifications": len(notifications),
        }

    async def run(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        *,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        items = await self.collect(start_time, end_time, cursor)
        processed = await self.process(items, now=now)
        return {"collected": len(items), **processed}

    def overview(self) -> dict[str, Any]:
        events = self.repository.list_events(limit=10)
        themes = self.repository.list_themes(limit=10)
        brief = self.repository.get_morning_brief()
        return {
            "counts": self.repository.counts(),
            "hot_events": [event.to_dict() for event in events],
            "hot_themes": [theme.to_dict() for theme in themes],
            "morning_brief": brief.to_dict() if brief is not None else None,
        }

    def list_events(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        available_before: datetime | None = None,
    ) -> list[MarketEvent]:
        return self.repository.list_events(
            limit=limit,
            event_type=event_type,
            available_before=available_before,
        )

    def list_themes(
        self,
        *,
        limit: int = 100,
        state: str | None = None,
    ) -> list[Theme]:
        return self.repository.list_themes(limit=limit, state=state)

    def get_morning_brief(self) -> InformationBrief | None:
        return self.repository.get_morning_brief()

    async def refresh(
        self,
        *,
        now: datetime | None = None,
        generate_brief: bool = True,
    ) -> dict[str, Any]:
        return await self.process(
            information=[],
            now=now,
            generate_brief=generate_brief,
        )

    def _generate_morning_brief(
        self,
        events: Sequence[MarketEvent],
        themes: Sequence[Theme],
        *,
        now: datetime,
    ) -> InformationBrief:
        important_events = sorted(
            (
                event
                for event in events
                if event.importance >= 60
            ),
            key=lambda event: (-event.importance, -event.heat_score),
        )
        policies = [
            event
            for event in important_events
            if event.event_type.value
            in {"POLICY", "MONETARY_POLICY", "REGULATION"}
        ]
        global_events = [
            event
            for event in important_events
            if event.event_type.value in {"GEOPOLITICAL", "COMMODITY"}
        ]
        risks = [
            event
            for event in important_events
            if event.event_type.value in {"RISK", "BLACK_SWAN"}
            or event.impact_direction.value == "NEGATIVE"
        ]
        current_hot = sorted(
            themes,
            key=lambda theme: (
                -theme.current_heat_score,
                -theme.forward_heat_score,
            ),
        )[:10]
        emerging = sorted(
            (
                theme
                for theme in themes
                if theme.forward_heat_score >= 50
                or theme.state.value in {"BREAKOUT", "INCUBATING"}
            ),
            key=lambda theme: (-theme.forward_heat_score, -theme.confidence),
        )[:10]
        stocks = _aggregate_stocks(important_events)
        content = {
            "market_environment": [
                event.to_dict()
                for event in important_events
                if event.event_type.value in {"MACRO", "MARKET"}
            ][:8],
            "important_policies": [event.to_dict() for event in policies[:8]],
            "global_events": [event.to_dict() for event in global_events[:8]],
            "current_hot_themes": [theme.to_dict() for theme in current_hot],
            "emerging_themes": [theme.to_dict() for theme in emerging],
            "key_stocks": stocks[:12],
            "risks": [event.to_dict() for event in risks[:8]],
        }
        generated_at = max(
            [now, *(event.last_update_time for event in events)],
        )
        brief_id = uuid5(
            NAMESPACE_URL,
            "https://xquant.local/intelligence/brief/morning/"
            f"{generated_at.date().isoformat()}",
        )
        return InformationBrief(
            id=brief_id,
            brief_type=BriefType.MORNING,
            title="X-QuantSentinel 盘前情报",
            summary=_brief_summary(events, themes),
            content=content,
            markdown=_brief_markdown(content),
            generated_at=generated_at,
            available_time=generated_at,
            event_ids=[event.id for event in important_events],
            theme_ids=[theme.id for theme in current_hot + emerging],
            metadata={
                "event_count": len(events),
                "theme_count": len(themes),
                "generated_at": utc_iso(generated_at),
            },
        )

    @staticmethod
    def _event_notifications(
        events: Sequence[MarketEvent],
        *,
        now: datetime,
    ) -> list[NotificationEvent]:
        notifications: list[NotificationEvent] = []
        for event in events:
            should_notify = (
                event.importance >= 75
                or event.event_type.value in {"RISK", "BLACK_SWAN", "POLICY"}
            )
            if not should_notify:
                continue
            severity = (
                NotificationSeverity.CRITICAL
                if event.importance >= 90
                or event.event_type.value == "BLACK_SWAN"
                else NotificationSeverity.IMPORTANT
            )
            dedup_key = hashlib.sha256(
                f"{event.id}:{event.last_update_time.isoformat()}".encode()
            ).hexdigest()
            notifications.append(
                NotificationEvent(
                    type="INTELLIGENCE_EVENT",
                    severity=severity,
                    title=event.title,
                    content=event.summary,
                    stock_ids=event.impact.affected_stocks,
                    theme_ids=[],
                    event_ids=[event.id],
                    channels=["web"],
                    dedup_key=dedup_key,
                    created_at=now,
                )
            )
        return notifications


def _aggregate_stocks(events: Sequence[MarketEvent]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        for stock in event.impact.affected_stocks:
            item = grouped.setdefault(
                stock,
                {
                    "stock_id": stock,
                    "event_ids": [],
                    "themes": [],
                    "directions": [],
                    "importance": 0.0,
                },
            )
            item["event_ids"].append(str(event.id))
            item["themes"].extend(event.impact.affected_themes)
            item["directions"].append(event.impact_direction.value)
            item["importance"] = max(item["importance"], event.importance)
    result = []
    for item in grouped.values():
        item["event_ids"] = list(dict.fromkeys(item["event_ids"]))
        item["themes"] = list(dict.fromkeys(item["themes"]))
        item["directions"] = list(dict.fromkeys(item["directions"]))
        result.append(item)
    return sorted(result, key=lambda item: (-item["importance"], item["stock_id"]))


def _brief_summary(
    events: Sequence[MarketEvent],
    themes: Sequence[Theme],
) -> str:
    important = sum(1 for event in events if event.importance >= 75)
    hot = sum(1 for theme in themes if theme.current_heat_score >= 60)
    return f"本期共聚合 {len(events)} 个事件，其中重要事件 {important} 个，高热主题 {hot} 个。"


def _brief_markdown(content: dict[str, Any]) -> str:
    sections = (
        ("今日市场环境", content["market_environment"]),
        ("重要政策", content["important_policies"]),
        ("国际重大事件", content["global_events"]),
        ("当前市场热点", content["current_hot_themes"]),
        ("潜在热点", content["emerging_themes"]),
        ("今日风险事件", content["risks"]),
    )
    lines = ["# X-QuantSentinel 盘前情报"]
    for title, items in sections:
        lines.extend(["", f"## {title}"])
        if not items:
            lines.append("- 暂无")
            continue
        for item in items:
            label = item.get("title") or item.get("name") or "未命名"
            lines.append(f"- {label}")
    return "\n".join(lines)


__all__ = ["IntelligenceService"]

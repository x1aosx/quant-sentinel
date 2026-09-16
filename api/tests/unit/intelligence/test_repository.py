from __future__ import annotations

from datetime import UTC, datetime

from xquant.intelligence.domain import (
    EventImpact,
    EventType,
    InformationBrief,
    MarketEvent,
    NotificationEvent,
    RawInformation,
    Theme,
)
from xquant.intelligence.repository import SqliteIntelligenceRepository
from xquant.registry.sqlite import Database


def test_sqlite_repository_is_idempotent_and_queries_persisted_objects(
    tmp_path,
) -> None:
    legacy = Database(tmp_path / "legacy.db")
    repository = SqliteIntelligenceRepository(legacy)
    information = RawInformation(
        source="example",
        source_type="NEWS",
        title="政策支持机器人产业",
        content="政策支持机器人产业发展。",
        publish_time=datetime(2026, 9, 15, 1, 0, tzinfo=UTC),
        fetch_time=datetime(2026, 9, 15, 1, 1, tzinfo=UTC),
    )
    event = MarketEvent(
        event_type=EventType.POLICY,
        title="政策支持机器人产业",
        summary="政策支持机器人产业发展。",
        importance=80,
        heat_score=75,
        source_count=2,
        impact=EventImpact(affected_themes=["机器人"]),
        first_publish_time=datetime(2026, 9, 15, 1, 0, tzinfo=UTC),
        last_update_time=datetime(2026, 9, 15, 1, 5, tzinfo=UTC),
    )
    theme = Theme(
        code="TH-ROBOT",
        name="机器人",
        current_heat_score=70,
        forward_heat_score=80,
        crowding_score=40,
        event_ids=[event.id],
        reasons=["聚合事件1个"],
    )
    brief = InformationBrief(title="盘前情报", summary="摘要")
    notification = NotificationEvent(title="政策事件", dedup_key="event-1")

    assert repository.save_information([information]) == 1
    assert repository.save_information([information]) == 1
    assert repository.save_events([event]) == 1
    assert repository.save_events([event]) == 1
    assert repository.save_themes([theme]) == 1
    assert repository.save_themes([theme]) == 1
    repository.save_brief(brief)
    repository.save_brief(brief)
    assert repository.save_notifications([notification]) == 1
    assert repository.save_notifications([notification]) == 1

    assert len(repository.list_information()) == 1
    assert len(repository.list_events()) == 1
    assert repository.get_event(str(event.id)) is not None
    assert len(repository.list_themes()) == 1
    assert repository.get_theme("TH-ROBOT") is not None
    assert repository.get_morning_brief() is not None
    assert len(repository.list_notifications()) == 1
    assert repository.counts() == {
        "information": 1,
        "events": 1,
        "themes": 1,
        "briefs": 1,
        "notifications": 1,
    }

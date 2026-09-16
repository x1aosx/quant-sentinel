from __future__ import annotations

from datetime import datetime
from typing import Any

from xquant.scheduler.application import TaskRegistry
from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    RetryPolicy,
    TaskContext,
    TaskDefinition,
    TaskResult,
)

from .domain import ensure_utc
from .service import IntelligenceService


class IntelligenceCollectHandler:
    def __init__(self, service: IntelligenceService) -> None:
        self.service = service

    async def execute(self, context: TaskContext) -> TaskResult:
        start_time = _optional_datetime(context.params.get("start_time"), "start_time")
        end_time = _optional_datetime(context.params.get("end_time"), "end_time")
        cursor = context.params.get("cursor")
        items = await self.service.collect(
            start_time,
            end_time,
            str(cursor) if cursor not in (None, "") else None,
        )
        return TaskResult(
            success=True,
            data={"information_ids": [str(item.id) for item in items]},
            metrics={"collected_count": len(items)},
        )


class IntelligenceProcessHandler:
    def __init__(self, service: IntelligenceService) -> None:
        self.service = service

    async def execute(self, context: TaskContext) -> TaskResult:
        now = _optional_datetime(context.params.get("now"), "now")
        result = await self.service.process(now=now)
        return TaskResult(
            success=True,
            data=result,
            metrics={
                "normalized_count": int(result["normalized"]),
                "event_count": int(result["events"]),
                "theme_count": int(result["themes"]),
                "duplicate_count": int(result["duplicates"]),
            },
        )


class IntelligenceRefreshHandler(IntelligenceProcessHandler):
    """Refresh events, themes, and the morning brief from persisted information."""

    async def execute(self, context: TaskContext) -> TaskResult:
        now = _optional_datetime(context.params.get("now"), "now")
        result = await self.service.refresh(now=now)
        return TaskResult(
            success=True,
            data=result,
            metrics={
                "event_count": int(result["events"]),
                "theme_count": int(result["themes"]),
                "notification_count": int(result["notifications"]),
            },
        )


class IntelligenceMorningBriefHandler:
    def __init__(self, service: IntelligenceService) -> None:
        self.service = service

    async def execute(self, context: TaskContext) -> TaskResult:
        now = _optional_datetime(context.params.get("now"), "now")
        result = await self.service.refresh(now=now, generate_brief=True)
        brief = self.service.get_morning_brief()
        return TaskResult(
            success=True,
            data={
                "brief": brief.to_dict() if brief is not None else None,
                **result,
            },
            metrics={"brief_generated": int(brief is not None)},
        )


def register_intelligence_tasks(
    registry: TaskRegistry,
    service: IntelligenceService,
) -> None:
    retry_policy = RetryPolicy(
        max_attempts=3,
        strategy="exponential",
        initial_delay_seconds=5,
        max_delay_seconds=120,
        jitter=True,
        no_retry_on=("ValueError", "TypeError"),
    )
    registry.register(
        TaskDefinition(
            name="intelligence.collect",
            handler="IntelligenceCollectHandler",
            description="采集新闻、政策和公告信息",
            timeout_seconds=300,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="market-data",
            priority=4,
            rate_limit_key="provider:intelligence",
        ),
        IntelligenceCollectHandler(service),
    )
    registry.register(
        TaskDefinition(
            name="intelligence.process",
            handler="IntelligenceProcessHandler",
            description="标准化、去重、聚类并分析情报",
            timeout_seconds=600,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="strategy",
            priority=5,
        ),
        IntelligenceProcessHandler(service),
    )
    registry.register(
        TaskDefinition(
            name="intelligence.brief.morning",
            handler="IntelligenceMorningBriefHandler",
            description="刷新盘前情报快报",
            timeout_seconds=300,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="notification",
            priority=3,
        ),
        IntelligenceMorningBriefHandler(service),
    )


def _optional_datetime(value: Any, field_name: str) -> datetime | None:
    return ensure_utc(value, field_name=field_name)


__all__ = [
    "IntelligenceCollectHandler",
    "IntelligenceMorningBriefHandler",
    "IntelligenceProcessHandler",
    "IntelligenceRefreshHandler",
    "register_intelligence_tasks",
]

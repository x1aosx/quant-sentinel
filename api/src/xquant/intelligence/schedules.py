"""市场情报定时自动更新计划：把「情报自动更新」维护成单条调度计划。

与数据中心自动同步（DatasetSyncScheduleService）同一套模式：
- 固定计划 id，重复设置不会产生重复计划；
- params 带 origin 标记，调度页里可识别来源；
- 启停 / 修改间隔都落到 scheduler 的 schedule repository，由引擎统一执行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from xquant.scheduler.domain import (
    IntervalTrigger,
    MisfirePolicy,
    ScheduleDefinition,
)

logger = logging.getLogger(__name__)

INTELLIGENCE_ORIGIN = "intelligence.auto-refresh"
INTELLIGENCE_TASK = "intelligence.run"
SYNC_TIMEZONE = "Asia/Shanghai"

MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 86_400
DEFAULT_INTERVAL_SECONDS = 1_800
DEFAULT_MAX_CATCH_UP_RUNS = 30
INTELLIGENCE_SCHEDULE_ID = "intelligence-auto-refresh"


def normalize_interval_seconds(interval_seconds: Any) -> int:
    """校验自动更新间隔，越界抛 ValueError。"""

    if interval_seconds is None:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(float(interval_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("自动更新间隔必须是秒数") from exc
    if value < MIN_INTERVAL_SECONDS:
        raise ValueError(f"自动更新间隔不能小于 {MIN_INTERVAL_SECONDS} 秒")
    if value > MAX_INTERVAL_SECONDS:
        raise ValueError(f"自动更新间隔不能大于 {MAX_INTERVAL_SECONDS} 秒")
    return value


def is_intelligence_schedule_params(params: Any) -> bool:
    if not isinstance(params, dict):
        return False
    return str(params.get("origin") or "") == INTELLIGENCE_ORIGIN


class IntelligenceScheduleService:
    """维护市场情报的定时自动更新计划（全局唯一一条）。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def get_status(self) -> dict[str, Any]:
        schedule = await self._current()
        if schedule is None:
            return {
                "schedule_id": None,
                "task_name": INTELLIGENCE_TASK,
                "enabled": False,
                "interval_seconds": None,
                "timezone": SYNC_TIMEZONE,
                "next_fire_at": None,
            }
        return self._describe(schedule)

    async def set_auto_refresh(
        self,
        *,
        enabled: bool,
        interval_seconds: Any = None,
    ) -> dict[str, Any]:
        if not self._task_available():
            raise LookupError("市场情报任务未注册（调度模块未启用）")
        current = await self._current()
        resolved_interval = normalize_interval_seconds(
            interval_seconds
            if interval_seconds is not None
            else (_interval_seconds(current) if current is not None else None)
        )
        if not enabled:
            if current is None:
                return {
                    "schedule_id": None,
                    "task_name": INTELLIGENCE_TASK,
                    "enabled": False,
                    "interval_seconds": None,
                    "timezone": SYNC_TIMEZONE,
                    "next_fire_at": None,
                }
            saved = await self._save(
                replace(current, enabled=False, next_fire_at=None),
                recompute_next=False,
            )
            return self._describe(saved)

        if current is None:
            schedule = self._build_schedule(interval_seconds=resolved_interval, enabled=True)
        else:
            schedule = replace(
                current,
                trigger=IntervalTrigger(interval_seconds=resolved_interval, timezone=SYNC_TIMEZONE),
                enabled=True,
            )
        saved = await self._save(schedule)
        return self._describe(saved)

    # ------------------------------------------------------------------ 内部实现

    def _task_available(self) -> bool:
        try:
            self.runtime.service.get_task(INTELLIGENCE_TASK)
            return True
        except KeyError:
            return False

    async def _current(self) -> ScheduleDefinition | None:
        for schedule in await self.runtime.service.list_schedules():
            if is_intelligence_schedule_params(schedule.params):
                return schedule
        return None

    def _build_schedule(
        self,
        *,
        interval_seconds: int,
        enabled: bool,
    ) -> ScheduleDefinition:
        return ScheduleDefinition(
            id=INTELLIGENCE_SCHEDULE_ID,
            task_name=INTELLIGENCE_TASK,
            trigger=IntervalTrigger(interval_seconds=interval_seconds, timezone=SYNC_TIMEZONE),
            params={
                "origin": INTELLIGENCE_ORIGIN,
                "interval_seconds": interval_seconds,
            },
            timezone=SYNC_TIMEZONE,
            misfire_policy=MisfirePolicy.FIRE_ONCE,
            enabled=enabled,
            max_catch_up_runs=DEFAULT_MAX_CATCH_UP_RUNS,
        )

    async def _save(
        self,
        schedule: ScheduleDefinition,
        *,
        recompute_next: bool = True,
    ) -> ScheduleDefinition:
        saved = await self.runtime.service.save_schedule(schedule, recompute_next=recompute_next)
        engine = getattr(self.runtime, "engine", None)
        if engine is not None and getattr(engine, "running", False):
            await engine.remove_schedule(schedule.id)
            if saved.enabled:
                await engine.add_schedule(saved)
        return saved

    @staticmethod
    def _describe(schedule: ScheduleDefinition) -> dict[str, Any]:
        next_fire_at = schedule.next_fire_at
        return {
            "schedule_id": schedule.id,
            "task_name": schedule.task_name,
            "enabled": bool(schedule.enabled),
            "interval_seconds": _interval_seconds(schedule),
            "timezone": str(schedule.timezone),
            "next_fire_at": next_fire_at.isoformat() if next_fire_at else None,
        }


def _interval_seconds(schedule: ScheduleDefinition) -> int | None:
    trigger = schedule.trigger
    seconds = getattr(trigger, "interval", None)
    if seconds is None:
        return None
    interval = getattr(seconds, "total_seconds", None)
    if not callable(interval):
        return None
    return int(cast("Callable[[], float]", interval)())


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "INTELLIGENCE_ORIGIN",
    "INTELLIGENCE_SCHEDULE_ID",
    "INTELLIGENCE_TASK",
    "IntelligenceScheduleService",
    "is_intelligence_schedule_params",
    "normalize_interval_seconds",
]

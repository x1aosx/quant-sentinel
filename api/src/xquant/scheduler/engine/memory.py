from __future__ import annotations

from xquant.scheduler.application.scheduler_service import SchedulerService
from xquant.scheduler.domain import ScheduleDefinition

from .base import SchedulerEngine


class MemorySchedulerEngine(SchedulerEngine):
    """Engine for tests and embedded runtimes that do not start a clock."""

    def __init__(self, service: SchedulerService) -> None:
        self.service = service
        self._schedules: dict[str, ScheduleDefinition] = {}
        self._paused: set[str] = set()
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def shutdown(self) -> None:
        self._running = False
        self._schedules.clear()
        self._paused.clear()

    @property
    def running(self) -> bool:
        return self._running

    async def add_schedule(self, schedule: ScheduleDefinition) -> None:
        self._schedules[schedule.id] = schedule
        if not schedule.enabled:
            self._paused.add(schedule.id)

    async def remove_schedule(self, schedule_id: str) -> None:
        self._schedules.pop(schedule_id, None)
        self._paused.discard(schedule_id)

    async def pause_schedule(self, schedule_id: str) -> None:
        self._paused.add(schedule_id)

    async def resume_schedule(self, schedule_id: str) -> None:
        self._paused.discard(schedule_id)

    async def reload(self) -> None:
        self._schedules = {
            schedule.id: schedule
            for schedule in await self.service.list_schedules()
        }
        self._paused = {
            schedule.id
            for schedule in self._schedules.values()
            if not schedule.enabled
        }


__all__ = ["MemorySchedulerEngine"]

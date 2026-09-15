from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from xquant.scheduler.application import SchedulerService, TaskRegistry
from xquant.scheduler.dispatcher.base import TaskDispatcher
from xquant.scheduler.domain import (
    IntervalTrigger,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
    TaskResult,
)
from xquant.scheduler.engine import APSchedulerEngine
from xquant.scheduler.repository import (
    InMemoryExecutionRepository,
    InMemoryScheduleRepository,
    InMemoryTaskRepository,
)


class _Handler:
    async def execute(self, _context) -> TaskResult:
        return TaskResult(success=True)


class _Dispatcher(TaskDispatcher):
    async def dispatch(self, _execution: TaskExecution) -> None:
        return None


def test_apscheduler_engine_loads_enabled_schedules() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        registry.register(TaskDefinition(name="test", handler="_Handler"), _Handler())
        service = SchedulerService(
            registry,
            InMemoryTaskRepository(),
            InMemoryScheduleRepository(),
            InMemoryExecutionRepository(),
            _Dispatcher(),
            now=lambda: datetime(2026, 9, 15, 9, 31, tzinfo=UTC),
        )
        await service.save_schedule(
            ScheduleDefinition(
                id="interval",
                task_name="test",
                trigger=IntervalTrigger(60),
            )
        )

        engine = APSchedulerEngine(service)
        await engine.start()
        try:
            jobs = engine._scheduler.get_jobs()  # type: ignore[union-attr]
            assert {job.id for job in jobs} == {
                "scheduler:control:poll",
                "scheduler:interval",
            }
        finally:
            await engine.shutdown()

    asyncio.run(scenario())

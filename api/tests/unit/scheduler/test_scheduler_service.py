from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from xquant.scheduler.application import SchedulerService
from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    DateTrigger,
    ExecutionStatus,
    IntervalTrigger,
    MisfirePolicy,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
    TaskResult,
)
from xquant.scheduler.repository import (
    InMemoryExecutionRepository,
    InMemoryScheduleRepository,
    InMemoryTaskRepository,
)


class _Handler:
    async def execute(self, context) -> TaskResult:
        return TaskResult(success=True, data={"execution_id": context.execution_id})


class _Dispatcher:
    def __init__(self) -> None:
        self.executions: list[TaskExecution] = []

    async def dispatch(self, execution: TaskExecution) -> None:
        self.executions.append(execution)


def _service(
    *,
    now: datetime,
) -> tuple[SchedulerService, _Dispatcher, InMemoryExecutionRepository]:
    from xquant.scheduler.application import TaskRegistry

    registry = TaskRegistry()
    registry.register(
        TaskDefinition(
            name="test.task",
            handler="_Handler",
            queue="test",
            priority=2,
            concurrency_policy=ConcurrencyPolicy.FORBID,
        ),
        _Handler(),
    )
    execution_repository = InMemoryExecutionRepository()
    dispatcher = _Dispatcher()
    ids = iter(["trace-1", "trace-2", "trace-3", "trace-4"])

    service = SchedulerService(
        registry,
        InMemoryTaskRepository(),
        InMemoryScheduleRepository(),
        execution_repository,
        dispatcher,  # type: ignore[arg-type]
        now=lambda: now,
        id_factory=lambda: next(ids),
    )
    return service, dispatcher, execution_repository


def test_manual_run_creates_execution_then_dispatches() -> None:
    now = datetime(2026, 9, 15, 9, 31, tzinfo=UTC)
    service, dispatcher, repository = _service(now=now)

    async def scenario() -> None:
        await service.sync_registry()
        execution = await service.run_task("test.task", {"symbol": "600000"})
        assert execution.status is ExecutionStatus.QUEUED
        assert execution.params == {"symbol": "600000"}
        assert dispatcher.executions == [execution]
        assert await repository.get(execution.id) == execution

    asyncio.run(scenario())


def test_misfire_catch_up_respects_limit() -> None:
    now = datetime(2026, 9, 15, 9, 31, tzinfo=UTC)
    service, dispatcher, _repository = _service(now=now)
    schedule = ScheduleDefinition(
        id="catch-up",
        task_name="test.task",
        trigger=IntervalTrigger(60, start_at=now - timedelta(minutes=5)),
        misfire_policy=MisfirePolicy.CATCH_UP,
        max_catch_up_runs=2,
        next_fire_at=now - timedelta(minutes=2),
    )

    async def scenario() -> None:
        await service.save_schedule(schedule)
        executions = await service.fire_schedule(schedule.id)
        assert len(executions) == 2
        assert [item.scheduled_at for item in executions] == [
            now - timedelta(minutes=2),
            now - timedelta(minutes=1),
        ]
        assert len(dispatcher.executions) == 2
        updated = await service.get_schedule(schedule.id)
        assert updated.last_fire_at == now - timedelta(minutes=1)
        assert updated.next_fire_at == now + timedelta(minutes=1)

    asyncio.run(scenario())


def test_schedule_pause_and_resume_recomputes_next_fire() -> None:
    now = datetime(2026, 9, 15, 9, 31, tzinfo=UTC)
    service, _dispatcher, _repository = _service(now=now)
    schedule = ScheduleDefinition(
        id="pause-test",
        task_name="test.task",
        trigger=IntervalTrigger(60),
        next_fire_at=now + timedelta(minutes=3),
    )

    async def scenario() -> None:
        await service.save_schedule(schedule)
        paused = await service.set_schedule_enabled(schedule.id, False)
        assert paused.enabled is False
        resumed = await service.set_schedule_enabled(schedule.id, True)
        assert resumed.enabled is True
        assert resumed.next_fire_at == now + timedelta(minutes=1)

    asyncio.run(scenario())


def test_date_trigger_disables_schedule_after_firing() -> None:
    now = datetime(2026, 9, 15, 9, 31, tzinfo=UTC)
    service, _dispatcher, _repository = _service(now=now)
    schedule = ScheduleDefinition(
        id="one-shot",
        task_name="test.task",
        trigger=DateTrigger(now),
        next_fire_at=now,
    )

    async def scenario() -> None:
        await service.save_schedule(schedule)
        executions = await service.fire_schedule(schedule.id)
        assert len(executions) == 1
        updated = await service.get_schedule(schedule.id)
        assert updated.enabled is False
        assert updated.last_fire_at == now
        assert updated.next_fire_at is None

    asyncio.run(scenario())

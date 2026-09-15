from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from xquant.scheduler.domain import (
    ExecutionStatus,
    MisfirePolicy,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
    TaskResult,
)
from xquant.scheduler.repository import (
    ExecutionRepository,
    ScheduleRepository,
    TaskRepository,
)

from ..cancellation import CancellationManager
from ..dispatcher import TaskDispatcher
from .planner import TaskPlan
from .task_registry import TaskNotFound, TaskRegistry


class SchedulerService:
    """Coordinate task registration, schedules, executions and dispatch."""

    def __init__(
        self,
        registry: TaskRegistry,
        task_repository: TaskRepository,
        schedule_repository: ScheduleRepository,
        execution_repository: ExecutionRepository,
        dispatcher: TaskDispatcher,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        max_catch_up_runs: int = 30,
        cancellation_manager: CancellationManager | None = None,
    ) -> None:
        self.registry = registry
        self.task_repository = task_repository
        self.schedule_repository = schedule_repository
        self.execution_repository = execution_repository
        self.dispatcher = dispatcher
        self._now = now or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self.max_catch_up_runs = max_catch_up_runs
        self.cancellation_manager = cancellation_manager

    async def sync_registry(self) -> list[TaskDefinition]:
        """Persist all in-process task definitions for management APIs."""

        definitions = self.registry.list()
        for definition in definitions:
            await self.task_repository.save(definition)
        return definitions

    async def list_tasks(self) -> list[TaskDefinition]:
        return await self.task_repository.list()

    async def get_task(self, name: str) -> TaskDefinition:
        definition = await self.task_repository.get(name)
        if definition is None:
            with suppress(TaskNotFound):
                definition = self.registry.get_definition(name)
        if definition is None:
            raise KeyError(name)
        return definition

    async def list_schedules(
        self,
        *,
        enabled: bool | None = None,
    ) -> list[ScheduleDefinition]:
        return await self.schedule_repository.list(enabled=enabled)

    async def get_schedule(self, schedule_id: str) -> ScheduleDefinition:
        schedule = await self.schedule_repository.get(schedule_id)
        if schedule is None:
            raise KeyError(schedule_id)
        return schedule

    async def save_schedule(
        self,
        schedule: ScheduleDefinition,
        *,
        recompute_next: bool = False,
    ) -> ScheduleDefinition:
        await self.get_task(schedule.task_name)
        now = self._now()
        next_fire_at = schedule.next_fire_at
        if schedule.enabled and (recompute_next or next_fire_at is None):
            next_fire_at = schedule.trigger.next_fire_time(
                schedule.last_fire_at,
                now,
            )
        normalized = replace(
            schedule,
            next_fire_at=next_fire_at,
            created_at=schedule.created_at or now,
            updated_at=now,
        )
        return await self.schedule_repository.save(normalized)

    async def delete_schedule(self, schedule_id: str) -> bool:
        return await self.schedule_repository.delete(schedule_id)

    async def set_schedule_enabled(
        self,
        schedule_id: str,
        enabled: bool,
    ) -> ScheduleDefinition:
        schedule = await self.get_schedule(schedule_id)
        next_fire_at = schedule.next_fire_at
        if enabled:
            next_fire_at = schedule.trigger.next_fire_time(
                schedule.last_fire_at,
                self._now(),
            )
        updated = await self.schedule_repository.update_state(
            schedule.id,
            next_fire_at=next_fire_at,
            enabled=enabled,
        )
        if updated is None:
            raise KeyError(schedule_id)
        return updated

    async def run_task(
        self,
        task_name: str,
        params: Mapping[str, Any] | None = None,
        *,
        schedule_id: str | None = None,
        scheduled_at: datetime | None = None,
        parent_execution_id: str | None = None,
        trace_id: str | None = None,
        priority: int | None = None,
        dispatch: bool = True,
    ) -> TaskExecution:
        definition = await self.get_task(task_name)
        now = self._now()
        execution = self._build_execution(
            definition,
            params=params,
            schedule_id=schedule_id,
            scheduled_at=scheduled_at,
            parent_execution_id=parent_execution_id,
            trace_id=trace_id,
            priority=priority,
            now=now,
        )
        await self.execution_repository.save(execution)
        if definition.planner is not None:
            return await self._run_planned_execution(
                execution,
                definition,
                dispatch=dispatch,
            )
        if dispatch:
            await self.dispatch(execution)
        return execution

    def _build_execution(
        self,
        definition: TaskDefinition,
        *,
        params: Mapping[str, Any] | None,
        schedule_id: str | None,
        scheduled_at: datetime | None,
        parent_execution_id: str | None,
        trace_id: str | None,
        priority: int | None,
        now: datetime,
        depends_on: tuple[str, ...] = (),
    ) -> TaskExecution:
        return TaskExecution(
            id=self._id_factory(),
            task_name=definition.name,
            schedule_id=schedule_id,
            parent_execution_id=parent_execution_id,
            depends_on=depends_on,
            queue=definition.queue,
            priority=definition.priority if priority is None else priority,
            status=ExecutionStatus.QUEUED,
            params=dict(params or {}),
            scheduled_at=scheduled_at or now,
            queued_at=now,
            max_attempts=(
                definition.retry_policy.max_attempts
                if definition.retry_policy is not None
                else 1
            ),
            trace_id=trace_id or self._id_factory(),
            created_at=now,
            updated_at=now,
        )

    async def _run_planned_execution(
        self,
        root: TaskExecution,
        definition: TaskDefinition,
        *,
        dispatch: bool,
    ) -> TaskExecution:
        started_at = self._now()
        try:
            planner = self.registry.get_planner(definition.planner or "")
            plan = await planner.plan(root)
            if not isinstance(plan, TaskPlan):
                raise TypeError("task planner must return TaskPlan")

            ordered_nodes = plan.topological_order()
            definitions: dict[str, TaskDefinition] = {}
            for node in ordered_nodes:
                definitions[node.key] = await self.get_task(node.task_name)

            key_to_execution_id: dict[str, str] = {}
            children: list[TaskExecution] = []
            for node in ordered_nodes:
                child_definition = definitions[node.key]
                child = self._build_execution(
                    child_definition,
                    params=node.params,
                    schedule_id=root.schedule_id,
                    scheduled_at=root.scheduled_at,
                    parent_execution_id=root.id,
                    trace_id=root.trace_id,
                    priority=node.priority,
                    now=self._now(),
                    depends_on=tuple(
                        key_to_execution_id[dependency]
                        for dependency in node.depends_on
                    ),
                )
                if node.queue is not None:
                    child.queue = node.queue
                key_to_execution_id[node.key] = child.id
                children.append(child)

            for child in children:
                await self.execution_repository.save(child)

            finished_at = self._now()
            root.started_at = started_at
            root.status = ExecutionStatus.SUCCESS
            root.finished_at = finished_at
            root.duration_ms = max(
                0.0,
                (finished_at - started_at).total_seconds() * 1000,
            )
            root.result = TaskResult(
                success=True,
                message=f"created {len(children)} planned executions",
                data={
                    "plan": plan.to_dict(),
                    "child_execution_ids": [
                        child.id
                        for child in children
                    ],
                },
            )
            root.updated_at = finished_at
            await self.execution_repository.save(root)
        except Exception as exc:
            finished_at = self._now()
            root.status = ExecutionStatus.FAILED
            root.error_type = type(exc).__name__
            root.error_message = str(exc) or type(exc).__name__
            root.finished_at = finished_at
            root.result = TaskResult(
                success=False,
                message=root.error_message,
            )
            root.updated_at = finished_at
            await self.execution_repository.save(root)
            raise

        if dispatch:
            for child in children:
                await self.dispatch(child)
        return root

    async def fire_schedule(
        self,
        schedule_id: str,
        *,
        now: datetime | None = None,
    ) -> list[TaskExecution]:
        schedule = await self.get_schedule(schedule_id)
        if not schedule.enabled:
            return []

        current_time = now or self._now()
        due_times = self._due_times(schedule, current_time)
        if not due_times:
            next_fire_at = schedule.trigger.next_fire_time(
                schedule.last_fire_at,
                current_time,
            )
            if next_fire_at is None:
                await self.schedule_repository.save(
                    replace(
                        schedule,
                        next_fire_at=None,
                        enabled=False,
                        updated_at=current_time,
                    )
                )
            else:
                await self.schedule_repository.update_state(
                    schedule.id,
                    next_fire_at=next_fire_at,
                )
            return []

        executions = [
            await self.run_task(
                schedule.task_name,
                schedule.params,
                schedule_id=schedule.id,
                scheduled_at=due_at,
                trace_id=self._id_factory(),
            )
            for due_at in due_times
        ]
        last_fire_at = due_times[-1]
        next_fire_at = schedule.trigger.next_fire_time(last_fire_at, current_time)
        if next_fire_at is None:
            await self.schedule_repository.save(
                replace(
                    schedule,
                    last_fire_at=last_fire_at,
                    next_fire_at=None,
                    enabled=False,
                    updated_at=current_time,
                )
            )
            return executions
        await self.schedule_repository.update_state(
            schedule.id,
            last_fire_at=last_fire_at,
            next_fire_at=next_fire_at,
        )
        return executions

    async def retry_execution(
        self,
        execution_id: str,
        *,
        now: datetime | None = None,
    ) -> TaskExecution:
        previous = await self.execution_repository.get(execution_id)
        if previous is None:
            raise KeyError(execution_id)
        if previous.status not in {
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.SKIPPED,
        }:
            raise ValueError("only terminal failed executions can be retried")
        current_time = now or self._now()
        execution = replace(
            previous,
            id=self._id_factory(),
            status=ExecutionStatus.QUEUED,
            queued_at=current_time,
            started_at=None,
            finished_at=None,
            attempt=1,
            worker_id=None,
            result=None,
            error_type=None,
            error_message=None,
            duration_ms=None,
            created_at=current_time,
            updated_at=current_time,
        )
        await self.execution_repository.save(execution)
        await self.dispatch(execution)
        return execution

    async def cancel_execution(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
    ) -> TaskExecution:
        execution = await self.execution_repository.get(execution_id)
        if execution is None:
            raise KeyError(execution_id)
        if execution.status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.SKIPPED,
        }:
            return execution
        if self.cancellation_manager is None:
            raise RuntimeError("cancellation manager is not configured")

        await self.cancellation_manager.request_cancel(execution_id, reason)
        if execution.status is not ExecutionStatus.RUNNING:
            execution.status = ExecutionStatus.CANCELLED
            execution.finished_at = self._now()
            execution.error_type = "CancelledError"
            execution.error_message = reason or "task execution was cancelled"
            execution.updated_at = execution.finished_at
            execution.result = TaskResult(
                success=False,
                message=execution.error_message,
            )
            await self.execution_repository.save(execution)
        return execution

    async def dispatch(self, execution: TaskExecution) -> None:
        try:
            await self.dispatcher.dispatch(execution)
        except Exception as exc:
            execution.status = ExecutionStatus.FAILED
            execution.error_type = type(exc).__name__
            execution.error_message = str(exc) or type(exc).__name__
            execution.finished_at = self._now()
            execution.updated_at = execution.finished_at
            await self.execution_repository.save(execution)
            raise

    def _due_times(
        self,
        schedule: ScheduleDefinition,
        now: datetime,
    ) -> list[datetime]:
        policy = MisfirePolicy(schedule.misfire_policy)
        cursor = schedule.next_fire_at
        if cursor is None:
            cursor = schedule.trigger.next_fire_time(schedule.last_fire_at, now)
        if cursor is None or cursor > now:
            return []

        if policy is MisfirePolicy.SKIP:
            if now - cursor > timedelta(seconds=60):
                return []
            return [cursor]
        if policy is MisfirePolicy.FIRE_ONCE:
            return [cursor]

        limit = min(
            schedule.max_catch_up_runs,
            self.max_catch_up_runs,
        )
        if limit <= 0:
            return [cursor]

        due_times: list[datetime] = []
        previous = schedule.last_fire_at
        candidate = cursor
        while candidate is not None and candidate <= now and len(due_times) < limit:
            due_times.append(candidate)
            previous = candidate
            candidate = schedule.trigger.next_fire_time(previous, previous)
        return due_times


__all__ = ["SchedulerService"]

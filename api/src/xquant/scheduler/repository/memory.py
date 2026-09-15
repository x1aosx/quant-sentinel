from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from xquant.scheduler.domain import (
    ExecutionStatus,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
)

from ._serialization import dumps, enum_value, loads, to_domain
from .base import ExecutionRepository, ScheduleRepository, TaskRepository


class InMemoryTaskRepository(TaskRepository):
    """Thread-safe JSON snapshot repository for local development and tests."""

    def __init__(self) -> None:
        self._rows: dict[str, str] = {}
        self._lock = RLock()

    async def save(self, task: TaskDefinition) -> TaskDefinition:
        payload = dumps(task)
        with self._lock:
            self._rows[task.name] = payload
        return to_domain(loads(payload), TaskDefinition)

    async def get(self, name: str) -> TaskDefinition | None:
        with self._lock:
            payload = self._rows.get(name)
        if payload is None:
            return None
        return to_domain(loads(payload), TaskDefinition)

    async def list(self) -> list[TaskDefinition]:
        with self._lock:
            rows = [self._rows[key] for key in sorted(self._rows)]
        return [to_domain(loads(row), TaskDefinition) for row in rows]


class InMemoryScheduleRepository(ScheduleRepository):
    """Thread-safe JSON snapshot repository for local development and tests."""

    def __init__(self) -> None:
        self._rows: dict[str, str] = {}
        self._lock = RLock()

    async def save(self, schedule: ScheduleDefinition) -> ScheduleDefinition:
        payload = dumps(schedule)
        with self._lock:
            self._rows[schedule.id] = payload
        return to_domain(loads(payload), ScheduleDefinition)

    async def get(self, schedule_id: str) -> ScheduleDefinition | None:
        with self._lock:
            payload = self._rows.get(schedule_id)
        if payload is None:
            return None
        return to_domain(loads(payload), ScheduleDefinition)

    async def list(self, enabled: bool | None = None) -> list[ScheduleDefinition]:
        with self._lock:
            rows = [self._rows[key] for key in sorted(self._rows)]
        schedules = [to_domain(loads(row), ScheduleDefinition) for row in rows]
        if enabled is None:
            return schedules
        return [schedule for schedule in schedules if schedule.enabled is enabled]

    async def delete(self, schedule_id: str) -> bool:
        with self._lock:
            return self._rows.pop(schedule_id, None) is not None

    async def update_state(
        self,
        schedule_id: str,
        *,
        last_fire_at: datetime | None = None,
        next_fire_at: datetime | None = None,
        enabled: bool | None = None,
    ) -> ScheduleDefinition | None:
        changes = {
            key: value
            for key, value in {
                "last_fire_at": last_fire_at,
                "next_fire_at": next_fire_at,
                "enabled": enabled,
            }.items()
            if value is not None
        }
        with self._lock:
            payload = self._rows.get(schedule_id)
            if payload is None:
                return None
            schedule = to_domain(loads(payload), ScheduleDefinition)
            updated = replace(schedule, **changes) if changes else schedule
            self._rows[schedule_id] = dumps(updated)
        return to_domain(loads(self._rows[schedule_id]), ScheduleDefinition)


class InMemoryExecutionRepository(ExecutionRepository):
    """Thread-safe JSON snapshot repository for local development and tests."""

    def __init__(self) -> None:
        self._rows: dict[str, str] = {}
        self._logs: list[dict[str, Any]] = []
        self._lock = RLock()

    async def save(self, execution: TaskExecution) -> TaskExecution:
        payload = dumps(execution)
        with self._lock:
            self._rows[execution.id] = payload
        return to_domain(loads(payload), TaskExecution)

    async def get(self, execution_id: str) -> TaskExecution | None:
        with self._lock:
            payload = self._rows.get(execution_id)
        if payload is None:
            return None
        return to_domain(loads(payload), TaskExecution)

    async def claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        attempt: int,
    ) -> TaskExecution | None:
        if not worker_id:
            raise ValueError("worker_id cannot be empty")
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        with self._lock:
            payload = self._rows.get(execution_id)
            if payload is None:
                return None
            execution = to_domain(loads(payload), TaskExecution)
            if (
                execution.status
                not in {
                    ExecutionStatus.PENDING,
                    ExecutionStatus.QUEUED,
                    ExecutionStatus.WAITING,
                    ExecutionStatus.RETRYING,
                }
                or execution.attempt != attempt
            ):
                return None
            claimed_at = datetime.now(UTC)
            claimed = replace(
                execution,
                status=ExecutionStatus.RUNNING,
                worker_id=worker_id,
                started_at=execution.started_at or claimed_at,
                updated_at=claimed_at,
            )
            self._rows[execution_id] = dumps(claimed)
        return to_domain(loads(self._rows[execution_id]), TaskExecution)

    async def list(
        self,
        status: Any | None = None,
        task_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskExecution]:
        normalized_limit, normalized_offset = _pagination(limit, offset)
        expected_status = enum_value(status)
        with self._lock:
            rows = [self._rows[key] for key in self._rows]
        executions = [to_domain(loads(row), TaskExecution) for row in rows]
        executions = [
            execution
            for execution in executions
            if (expected_status is None or enum_value(execution.status) == expected_status)
            and (task_name is None or execution.task_name == task_name)
        ]
        executions.sort(key=_execution_sort_key, reverse=True)
        return executions[normalized_offset : normalized_offset + normalized_limit]

    async def find_stale_running(
        self,
        before: datetime,
        worker_ids: set[str] | None = None,
    ) -> list[TaskExecution]:
        if worker_ids is not None and not worker_ids:
            return []
        with self._lock:
            rows = [self._rows[key] for key in self._rows]
        stale: list[TaskExecution] = []
        for row in rows:
            execution = to_domain(loads(row), TaskExecution)
            if enum_value(execution.status) not in {"RUNNING", "RETRYING"}:
                continue
            if worker_ids is not None and execution.worker_id not in worker_ids:
                continue
            started_at = _execution_activity_at(execution)
            if started_at is not None and started_at < before:
                stale.append(execution)
        stale.sort(key=_execution_sort_key)
        return stale

    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        entry = {
            "execution_id": execution_id,
            "level": level,
            "event": event,
            "message": message,
            "data": dict(data or {}),
        }
        with self._lock:
            self._logs.append(entry)


def _pagination(limit: int, offset: int) -> tuple[int, int]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    return limit, offset


def _execution_sort_key(execution: TaskExecution) -> tuple[str, str]:
    return (str(getattr(execution, "created_at", "") or ""), execution.id)


def _execution_activity_at(execution: TaskExecution) -> datetime | None:
    for field_name in ("started_at", "queued_at", "scheduled_at", "created_at"):
        value = getattr(execution, field_name, None)
        if isinstance(value, datetime):
            return value
    return None


__all__ = [
    "InMemoryExecutionRepository",
    "InMemoryScheduleRepository",
    "InMemoryTaskRepository",
]

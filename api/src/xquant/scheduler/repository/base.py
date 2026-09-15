from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from xquant.scheduler.domain import (
    ExecutionStatus,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
)


class TaskRepository(ABC):
    @abstractmethod
    async def save(self, task: TaskDefinition) -> TaskDefinition:
        """Create or replace a task definition."""

    @abstractmethod
    async def get(self, name: str) -> TaskDefinition | None:
        """Return a task definition by name."""

    @abstractmethod
    async def list(self) -> list[TaskDefinition]:
        """List all task definitions."""


class ScheduleRepository(ABC):
    @abstractmethod
    async def save(self, schedule: ScheduleDefinition) -> ScheduleDefinition:
        """Create or replace a schedule definition."""

    @abstractmethod
    async def get(self, schedule_id: str) -> ScheduleDefinition | None:
        """Return a schedule definition by id."""

    @abstractmethod
    async def list(self, enabled: bool | None = None) -> list[ScheduleDefinition]:
        """List schedules, optionally filtered by enabled state."""

    @abstractmethod
    async def delete(self, schedule_id: str) -> bool:
        """Delete a schedule and report whether it existed."""

    @abstractmethod
    async def update_state(
        self,
        schedule_id: str,
        *,
        last_fire_at: datetime | None = None,
        next_fire_at: datetime | None = None,
        enabled: bool | None = None,
    ) -> ScheduleDefinition | None:
        """Update persisted scheduling state without replacing the definition."""


class ExecutionRepository(ABC):
    @abstractmethod
    async def save(self, execution: TaskExecution) -> TaskExecution:
        """Create or replace an execution."""

    @abstractmethod
    async def get(self, execution_id: str) -> TaskExecution | None:
        """Return an execution by id."""

    @abstractmethod
    async def claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        attempt: int,
    ) -> TaskExecution | None:
        """Atomically claim a queued execution for one worker attempt."""

    @abstractmethod
    async def list(
        self,
        status: ExecutionStatus | str | None = None,
        task_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskExecution]:
        """List executions, newest first, with core filters and pagination."""

    @abstractmethod
    async def find_stale_running(
        self,
        before: datetime,
        worker_ids: set[str] | None = None,
    ) -> list[TaskExecution]:
        """Find RUNNING executions older than the supplied threshold."""

    @abstractmethod
    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """Append a structured execution log entry."""


__all__ = [
    "ExecutionRepository",
    "ScheduleRepository",
    "TaskRepository",
]

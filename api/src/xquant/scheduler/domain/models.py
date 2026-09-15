from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ._time import coerce_timezone, require_aware
from .enums import ConcurrencyPolicy, ExecutionStatus, MisfirePolicy
from .policies import RetryPolicy
from .triggers import Trigger, load_trigger


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """A reusable description of work that the scheduler can execute."""

    name: str
    handler: str
    description: str | None = None
    timeout_seconds: int = 300
    retry_policy: RetryPolicy | None = None
    concurrency_policy: ConcurrencyPolicy | str = ConcurrencyPolicy.FORBID
    queue: str = "default"
    priority: int = 5
    rate_limit_key: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("task name cannot be empty")
        if not self.handler.strip():
            raise ValueError("task handler cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.queue.strip():
            raise ValueError("queue cannot be empty")
        if not 0 <= self.priority <= 9:
            raise ValueError("priority must be between 0 and 9")
        object.__setattr__(
            self,
            "concurrency_policy",
            ConcurrencyPolicy(self.concurrency_policy),
        )


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    """Bind a task to a trigger and runtime policy."""

    id: str
    task_name: str
    trigger: Trigger
    params: Mapping[str, Any] = field(default_factory=dict)
    calendar: str | None = None
    timezone: str | ZoneInfo = "Asia/Shanghai"
    misfire_policy: MisfirePolicy | str = MisfirePolicy.FIRE_ONCE
    enabled: bool = True
    max_catch_up_runs: int = 30
    last_fire_at: datetime | None = None
    next_fire_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("schedule id cannot be empty")
        if not self.task_name.strip():
            raise ValueError("task name cannot be empty")
        if self.max_catch_up_runs < 0:
            raise ValueError("max_catch_up_runs cannot be negative")
        object.__setattr__(self, "timezone", coerce_timezone(self.timezone))
        object.__setattr__(
            self,
            "misfire_policy",
            MisfirePolicy(self.misfire_policy),
        )
        for name in (
            "last_fire_at",
            "next_fire_at",
            "created_at",
            "updated_at",
        ):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)

    def to_dict(self) -> dict[str, Any]:
        """Return a stable, JSON-safe representation of this schedule."""

        return {
            "id": self.id,
            "task_name": self.task_name,
            "trigger": self.trigger.to_dict(),
            "params": dict(self.params),
            "calendar": self.calendar,
            "timezone": str(self.timezone),
            "misfire_policy": self.misfire_policy.value,
            "enabled": self.enabled,
            "max_catch_up_runs": self.max_catch_up_runs,
            "last_fire_at": _serialize_datetime(self.last_fire_at),
            "next_fire_at": _serialize_datetime(self.next_fire_at),
            "created_at": _serialize_datetime(self.created_at),
            "updated_at": _serialize_datetime(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScheduleDefinition:
        """Restore a schedule, including its concrete trigger implementation."""

        if not isinstance(payload, Mapping):
            raise TypeError("schedule payload must be a mapping")
        return cls(
            id=str(payload["id"]),
            task_name=str(payload["task_name"]),
            trigger=load_trigger(payload["trigger"]),
            params=dict(payload.get("params") or {}),
            calendar=payload.get("calendar"),
            timezone=str(payload.get("timezone", "Asia/Shanghai")),
            misfire_policy=payload.get("misfire_policy", MisfirePolicy.FIRE_ONCE),
            enabled=bool(payload.get("enabled", True)),
            max_catch_up_runs=int(payload.get("max_catch_up_runs", 30)),
            last_fire_at=_deserialize_datetime(
                payload.get("last_fire_at"),
                "last_fire_at",
            ),
            next_fire_at=_deserialize_datetime(
                payload.get("next_fire_at"),
                "next_fire_at",
            ),
            created_at=_deserialize_datetime(
                payload.get("created_at"),
                "created_at",
            ),
            updated_at=_deserialize_datetime(
                payload.get("updated_at"),
                "updated_at",
            ),
        )


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _deserialize_datetime(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        require_aware(value, field_name)
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        require_aware(parsed, field_name)
        return parsed
    raise TypeError(f"{field_name} must be an ISO datetime string or datetime")


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Normalized result returned by a task handler."""

    success: bool
    data: Mapping[str, Any] | None = None
    message: str | None = None
    metrics: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Execution-scoped data passed to a task handler."""

    task_name: str
    execution_id: str
    schedule_id: str | None
    scheduled_at: datetime
    started_at: datetime
    attempt: int
    params: Mapping[str, Any]
    trace_id: str
    worker_id: str | None = None
    cancellation_token: object | None = None

    def __post_init__(self) -> None:
        if not self.task_name.strip():
            raise ValueError("task_name cannot be empty")
        if not self.execution_id.strip():
            raise ValueError("execution_id cannot be empty")
        if not self.trace_id.strip():
            raise ValueError("trace_id cannot be empty")
        if self.attempt < 1:
            raise ValueError("attempt must be at least 1")
        require_aware(self.scheduled_at, "scheduled_at")
        require_aware(self.started_at, "started_at")


@dataclass(slots=True)
class TaskExecution:
    """One concrete execution instance of a task."""

    id: str
    task_name: str
    scheduled_at: datetime
    schedule_id: str | None = None
    parent_execution_id: str | None = None
    queue: str = "default"
    priority: int = 5
    status: ExecutionStatus | str = ExecutionStatus.PENDING
    params: Mapping[str, Any] = field(default_factory=dict)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempt: int = 1
    max_attempts: int = 1
    worker_id: str | None = None
    trace_id: str = ""
    result: TaskResult | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("execution id cannot be empty")
        if not self.task_name.strip():
            raise ValueError("task name cannot be empty")
        if not self.queue.strip():
            raise ValueError("queue cannot be empty")
        if not 0 <= self.priority <= 9:
            raise ValueError("priority must be between 0 and 9")
        if self.attempt < 1:
            raise ValueError("attempt must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")

        object.__setattr__(self, "status", ExecutionStatus(self.status))
        require_aware(self.scheduled_at, "scheduled_at")
        for name in (
            "queued_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """Structured lifecycle event emitted for a task execution."""

    event_type: str
    execution_id: str
    task_name: str
    occurred_at: datetime
    trace_id: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_type.strip():
            raise ValueError("event_type cannot be empty")
        if not self.execution_id.strip():
            raise ValueError("execution_id cannot be empty")
        if not self.task_name.strip():
            raise ValueError("task_name cannot be empty")
        if not self.trace_id.strip():
            raise ValueError("trace_id cannot be empty")
        require_aware(self.occurred_at, "occurred_at")


__all__ = [
    "ScheduleDefinition",
    "TaskContext",
    "TaskDefinition",
    "TaskEvent",
    "TaskExecution",
    "TaskResult",
]

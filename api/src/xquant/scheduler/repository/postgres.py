from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from xquant.scheduler.domain import (
    ExecutionStatus,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
)
from xquant.storage import PostgresStore

from ._serialization import (
    decode_json,
    dumps,
    enum_value,
    to_domain,
    trigger_to_payload,
)
from .base import ExecutionRepository, ScheduleRepository, TaskRepository


class PostgresTaskRepository(TaskRepository):
    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    async def save(self, task: TaskDefinition) -> TaskDefinition:
        retry_policy = getattr(task, "retry_policy", None)
        self._store.execute(
            """
            INSERT INTO scheduler.scheduler_task
                (name, handler, description, queue, priority, timeout_seconds,
                 retry_policy_json, concurrency_policy, rate_limit_key, enabled,
                 updated_at)
            VALUES
                (:name, :handler, :description, :queue, :priority,
                 :timeout_seconds, CAST(:retry_policy_json AS jsonb),
                 :concurrency_policy, :rate_limit_key, :enabled, now())
            ON CONFLICT (name) DO UPDATE SET
                handler = EXCLUDED.handler,
                description = EXCLUDED.description,
                queue = EXCLUDED.queue,
                priority = EXCLUDED.priority,
                timeout_seconds = EXCLUDED.timeout_seconds,
                retry_policy_json = EXCLUDED.retry_policy_json,
                concurrency_policy = EXCLUDED.concurrency_policy,
                rate_limit_key = EXCLUDED.rate_limit_key,
                enabled = EXCLUDED.enabled,
                updated_at = now()
            """,
            {
                "name": task.name,
                "handler": task.handler,
                "description": getattr(task, "description", None),
                "queue": getattr(task, "queue", "default"),
                "priority": getattr(task, "priority", 5),
                "timeout_seconds": getattr(task, "timeout_seconds", 300),
                "retry_policy_json": dumps(retry_policy),
                "concurrency_policy": enum_value(
                    getattr(task, "concurrency_policy", "FORBID")
                ),
                "rate_limit_key": getattr(task, "rate_limit_key", None),
                "enabled": getattr(task, "enabled", True),
            },
        )
        return task

    async def get(self, name: str) -> TaskDefinition | None:
        row = self._store.query_one(
            """
            SELECT name, handler, description, queue, priority, timeout_seconds,
                   retry_policy_json, concurrency_policy, rate_limit_key, enabled
            FROM scheduler.scheduler_task
            WHERE name = :name
            """,
            {"name": name},
        )
        return _task_from_row(row) if row is not None else None

    async def list(self) -> list[TaskDefinition]:
        rows = self._store.query(
            """
            SELECT name, handler, description, queue, priority, timeout_seconds,
                   retry_policy_json, concurrency_policy, rate_limit_key, enabled
            FROM scheduler.scheduler_task
            ORDER BY name
            """
        )
        return [_task_from_row(row) for row in rows]


class PostgresScheduleRepository(ScheduleRepository):
    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    async def save(self, schedule: ScheduleDefinition) -> ScheduleDefinition:
        trigger = getattr(schedule, "trigger", None)
        trigger_type, trigger_payload = _trigger_payload(trigger)
        self._store.execute(
            """
            INSERT INTO scheduler.scheduler_schedule
                (id, task_name, trigger_type, trigger_config_json, params_json,
                 calendar, timezone, misfire_policy, enabled, max_catch_up_runs,
                 last_fire_at, next_fire_at, created_at, updated_at)
            VALUES
                (:id, :task_name, :trigger_type, CAST(:trigger_config_json AS jsonb),
                 CAST(:params_json AS jsonb), :calendar, :timezone, :misfire_policy,
                 :enabled, :max_catch_up_runs, CAST(:last_fire_at AS timestamptz),
                 CAST(:next_fire_at AS timestamptz),
                 CAST(:created_at AS timestamptz), now())
            ON CONFLICT (id) DO UPDATE SET
                task_name = EXCLUDED.task_name,
                trigger_type = EXCLUDED.trigger_type,
                trigger_config_json = EXCLUDED.trigger_config_json,
                params_json = EXCLUDED.params_json,
                calendar = EXCLUDED.calendar,
                timezone = EXCLUDED.timezone,
                misfire_policy = EXCLUDED.misfire_policy,
                enabled = EXCLUDED.enabled,
                max_catch_up_runs = EXCLUDED.max_catch_up_runs,
                last_fire_at = EXCLUDED.last_fire_at,
                next_fire_at = EXCLUDED.next_fire_at,
                updated_at = now()
            """,
            {
                "id": schedule.id,
                "task_name": schedule.task_name,
                "trigger_type": trigger_type,
                "trigger_config_json": dumps(trigger_payload),
                "params_json": dumps(getattr(schedule, "params", {})),
                "calendar": getattr(schedule, "calendar", None),
                "timezone": str(
                    getattr(schedule, "timezone", "Asia/Shanghai")
                ),
                "misfire_policy": enum_value(
                    getattr(schedule, "misfire_policy", "FIRE_ONCE")
                ),
                "enabled": getattr(schedule, "enabled", True),
                "max_catch_up_runs": getattr(schedule, "max_catch_up_runs", 30),
                "last_fire_at": getattr(schedule, "last_fire_at", None),
                "next_fire_at": getattr(schedule, "next_fire_at", None),
                "created_at": getattr(schedule, "created_at", None) or datetime.now(UTC),
            },
        )
        return schedule

    async def get(self, schedule_id: str) -> ScheduleDefinition | None:
        row = self._store.query_one(
            """
            SELECT id, task_name, trigger_type, trigger_config_json, params_json,
                   calendar, timezone, misfire_policy, enabled, max_catch_up_runs,
                   last_fire_at, next_fire_at, created_at, updated_at
            FROM scheduler.scheduler_schedule
            WHERE id = :schedule_id
            """,
            {"schedule_id": schedule_id},
        )
        return _schedule_from_row(row) if row is not None else None

    async def list(self, enabled: bool | None = None) -> list[ScheduleDefinition]:
        if enabled is None:
            rows = self._store.query(
                """
                SELECT id, task_name, trigger_type, trigger_config_json, params_json,
                       calendar, timezone, misfire_policy, enabled, max_catch_up_runs,
                       last_fire_at, next_fire_at, created_at, updated_at
                FROM scheduler.scheduler_schedule
                ORDER BY id
                """
            )
        else:
            rows = self._store.query(
                """
                SELECT id, task_name, trigger_type, trigger_config_json, params_json,
                       calendar, timezone, misfire_policy, enabled, max_catch_up_runs,
                       last_fire_at, next_fire_at, created_at, updated_at
                FROM scheduler.scheduler_schedule
                WHERE enabled = :enabled
                ORDER BY id
                """,
                {"enabled": enabled},
            )
        return [_schedule_from_row(row) for row in rows]

    async def delete(self, schedule_id: str) -> bool:
        row = self._store.execute(
            """
            DELETE FROM scheduler.scheduler_schedule
            WHERE id = :schedule_id
            RETURNING id
            """,
            {"schedule_id": schedule_id},
            fetch="one",
        )
        return row is not None

    async def update_state(
        self,
        schedule_id: str,
        *,
        last_fire_at: datetime | None = None,
        next_fire_at: datetime | None = None,
        enabled: bool | None = None,
    ) -> ScheduleDefinition | None:
        assignments = ["updated_at = now()"]
        params: dict[str, Any] = {"schedule_id": schedule_id}
        if last_fire_at is not None:
            assignments.append("last_fire_at = CAST(:last_fire_at AS timestamptz)")
            params["last_fire_at"] = last_fire_at
        if next_fire_at is not None:
            assignments.append("next_fire_at = CAST(:next_fire_at AS timestamptz)")
            params["next_fire_at"] = next_fire_at
        if enabled is not None:
            assignments.append("enabled = :enabled")
            params["enabled"] = enabled
        self._store.execute(
            f"""
            UPDATE scheduler.scheduler_schedule
            SET {", ".join(assignments)}
            WHERE id = :schedule_id
            """,
            params,
        )
        return await self.get(schedule_id)


class PostgresExecutionRepository(ExecutionRepository):
    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    async def save(self, execution: TaskExecution) -> TaskExecution:
        now = datetime.now(UTC)
        result = getattr(execution, "result", None)
        self._store.execute(
            """
            INSERT INTO scheduler.scheduler_execution
                (id, task_name, schedule_id, parent_execution_id, queue, priority,
                 status, params_json, scheduled_at, queued_at, started_at,
                 finished_at, attempt, max_attempts, worker_id, trace_id,
                 result_json, error_type, error_message, duration_ms, created_at,
                 updated_at)
            VALUES
                (:id, :task_name, :schedule_id, :parent_execution_id, :queue,
                 :priority, :status, CAST(:params_json AS jsonb),
                 CAST(:scheduled_at AS timestamptz), CAST(:queued_at AS timestamptz),
                 CAST(:started_at AS timestamptz), CAST(:finished_at AS timestamptz),
                 :attempt, :max_attempts, :worker_id, :trace_id,
                 CAST(:result_json AS jsonb), :error_type, :error_message,
                 :duration_ms, CAST(:created_at AS timestamptz), now())
            ON CONFLICT (id) DO UPDATE SET
                task_name = EXCLUDED.task_name,
                schedule_id = EXCLUDED.schedule_id,
                parent_execution_id = EXCLUDED.parent_execution_id,
                queue = EXCLUDED.queue,
                priority = EXCLUDED.priority,
                status = EXCLUDED.status,
                params_json = EXCLUDED.params_json,
                scheduled_at = EXCLUDED.scheduled_at,
                queued_at = EXCLUDED.queued_at,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                attempt = EXCLUDED.attempt,
                max_attempts = EXCLUDED.max_attempts,
                worker_id = EXCLUDED.worker_id,
                trace_id = EXCLUDED.trace_id,
                result_json = EXCLUDED.result_json,
                error_type = EXCLUDED.error_type,
                error_message = EXCLUDED.error_message,
                duration_ms = EXCLUDED.duration_ms,
                updated_at = now()
            WHERE scheduler.scheduler_execution.status NOT IN
                    ('SUCCESS', 'FAILED', 'TIMEOUT', 'CANCELLED', 'SKIPPED')
            """,
            {
                "id": execution.id,
                "task_name": execution.task_name,
                "schedule_id": getattr(execution, "schedule_id", None),
                "parent_execution_id": getattr(
                    execution, "parent_execution_id", None
                ),
                "queue": getattr(execution, "queue", "default"),
                "priority": getattr(execution, "priority", 5),
                "status": enum_value(execution.status),
                "params_json": dumps(getattr(execution, "params", {})),
                "scheduled_at": getattr(execution, "scheduled_at", None),
                "queued_at": getattr(execution, "queued_at", None),
                "started_at": getattr(execution, "started_at", None),
                "finished_at": getattr(execution, "finished_at", None),
                "attempt": getattr(execution, "attempt", 1),
                "max_attempts": getattr(execution, "max_attempts", 1),
                "worker_id": getattr(execution, "worker_id", None),
                "trace_id": getattr(execution, "trace_id", ""),
                "result_json": dumps(result),
                "error_type": getattr(execution, "error_type", None),
                "error_message": getattr(execution, "error_message", None),
                "duration_ms": getattr(execution, "duration_ms", None),
                "created_at": getattr(execution, "created_at", now) or now,
            },
        )
        return execution

    async def get(self, execution_id: str) -> TaskExecution | None:
        row = self._store.query_one(
            _EXECUTION_SELECT + " WHERE id = :execution_id",
            {"execution_id": execution_id},
        )
        return _execution_from_row(row) if row is not None else None

    async def list(
        self,
        status: ExecutionStatus | str | None = None,
        task_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskExecution]:
        normalized_limit, normalized_offset = _pagination(limit, offset)
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": normalized_limit,
            "offset": normalized_offset,
        }
        status_value = enum_value(status)
        if status_value is not None:
            clauses.append("status = :status")
            params["status"] = status_value
        if task_name is not None:
            clauses.append("task_name = :task_name")
            params["task_name"] = task_name
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._store.query(
            _EXECUTION_SELECT
            + where
            + """
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return [_execution_from_row(row) for row in rows]

    async def find_stale_running(
        self,
        before: datetime,
        worker_ids: set[str] | None = None,
    ) -> list[TaskExecution]:
        if worker_ids is not None and not worker_ids:
            return []
        clauses = [
            "status IN ('RUNNING', 'RETRYING')",
            (
                "COALESCE(started_at, queued_at, scheduled_at, created_at) < "
                "CAST(:before AS timestamptz)"
            ),
        ]
        params: dict[str, Any] = {"before": before}
        if worker_ids is not None:
            placeholders: list[str] = []
            for index, worker_id in enumerate(sorted(worker_ids)):
                key = f"worker_id_{index}"
                placeholders.append(f":{key}")
                params[key] = worker_id
            clauses.append(f"worker_id IN ({', '.join(placeholders)})")
        rows = self._store.query(
            _EXECUTION_SELECT
            + " WHERE "
            + " AND ".join(clauses)
            + """
            ORDER BY COALESCE(started_at, queued_at, scheduled_at, created_at)
            """,
            params,
        )
        return [_execution_from_row(row) for row in rows]

    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        self._store.execute(
            """
            INSERT INTO scheduler.scheduler_execution_log
                (execution_id, level, event, message, data_json)
            VALUES
                (:execution_id, :level, :event, :message,
                 CAST(:data_json AS jsonb))
            """,
            {
                "execution_id": execution_id,
                "level": level,
                "event": event,
                "message": message,
                "data_json": dumps(dict(data or {})),
            },
        )


_EXECUTION_SELECT = """
    SELECT id, task_name, schedule_id, parent_execution_id, queue, priority,
           status, params_json, scheduled_at, queued_at, started_at, finished_at,
           attempt, max_attempts, worker_id, trace_id, result_json, error_type,
           error_message, duration_ms, created_at, updated_at
    FROM scheduler.scheduler_execution
"""


def _task_from_row(row: Mapping[str, Any]) -> TaskDefinition:
    return to_domain(
        {
            "name": row["name"],
            "handler": row["handler"],
            "description": row.get("description"),
            "timeout_seconds": row.get("timeout_seconds", 300),
            "retry_policy": decode_json(row.get("retry_policy_json"), None),
            "concurrency_policy": row.get("concurrency_policy", "FORBID"),
            "queue": row.get("queue", "default"),
            "priority": row.get("priority", 5),
            "rate_limit_key": row.get("rate_limit_key"),
            "enabled": bool(row.get("enabled", True)),
        },
        TaskDefinition,
    )


def _schedule_from_row(row: Mapping[str, Any]) -> ScheduleDefinition:
    trigger_payload = decode_json(row.get("trigger_config_json"), {})
    if isinstance(trigger_payload, Mapping):
        trigger_payload = {
            **trigger_payload,
            "type": row.get("trigger_type") or trigger_payload.get("type"),
        }
    return to_domain(
        {
            "id": row["id"],
            "task_name": row["task_name"],
            "trigger": trigger_payload,
            "params": decode_json(row.get("params_json"), {}),
            "calendar": row.get("calendar"),
            "timezone": row.get("timezone", "Asia/Shanghai"),
            "misfire_policy": row.get("misfire_policy", "FIRE_ONCE"),
            "enabled": bool(row.get("enabled", True)),
            "max_catch_up_runs": row.get("max_catch_up_runs", 30),
            "last_fire_at": row.get("last_fire_at"),
            "next_fire_at": row.get("next_fire_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        },
        ScheduleDefinition,
    )


def _execution_from_row(row: Mapping[str, Any]) -> TaskExecution:
    return to_domain(
        {
            "id": row["id"],
            "task_name": row["task_name"],
            "schedule_id": row.get("schedule_id"),
            "parent_execution_id": row.get("parent_execution_id"),
            "queue": row.get("queue", "default"),
            "priority": row.get("priority", 5),
            "status": row["status"],
            "params": decode_json(row.get("params_json"), {}),
            "scheduled_at": row.get("scheduled_at"),
            "queued_at": row.get("queued_at"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "attempt": row.get("attempt", 1),
            "max_attempts": row.get("max_attempts", 1),
            "worker_id": row.get("worker_id"),
            "trace_id": row.get("trace_id", ""),
            "result": decode_json(row.get("result_json"), None),
            "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "duration_ms": row.get("duration_ms"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        },
        TaskExecution,
    )


def _trigger_payload(trigger: Any) -> tuple[str, dict[str, Any]]:
    payload = trigger_to_payload(trigger)
    trigger_type = str(payload.get("type") or "unknown")
    return trigger_type, payload


def _pagination(limit: int, offset: int) -> tuple[int, int]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    return limit, offset


__all__ = [
    "PostgresExecutionRepository",
    "PostgresScheduleRepository",
    "PostgresTaskRepository",
]

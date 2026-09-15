from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from xquant.scheduler.domain import (
    CronTrigger,
    ExecutionStatus,
    RetryPolicy,
    ScheduleDefinition,
    TaskDefinition,
    TaskExecution,
)
from xquant.scheduler.repository import (
    InMemoryExecutionRepository,
    InMemoryScheduleRepository,
    InMemoryTaskRepository,
    PostgresExecutionRepository,
    PostgresScheduleRepository,
    PostgresTaskRepository,
    ensure_scheduler_schema,
)

NOW = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)


def _task(
    name: str = "market.kline.sync",
    *,
    description: str = "sync kline",
) -> TaskDefinition:
    return TaskDefinition(
        name=name,
        handler="KlineHandler",
        description=description,
        retry_policy=RetryPolicy(
            max_attempts=3,
            initial_delay_seconds=1,
            jitter=False,
        ),
        queue="market-data",
        priority=2,
    )


def _schedule(schedule_id: str = "schedule-1") -> ScheduleDefinition:
    return ScheduleDefinition(
        id=schedule_id,
        task_name="market.kline.sync",
        trigger=CronTrigger(
            minute="*/5",
            hour="9-15",
            day="*",
            month="*",
            day_of_week="1-5",
            timezone="Asia/Shanghai",
        ),
        params={"market": "CN"},
        calendar="SSE",
        timezone="Asia/Shanghai",
        max_catch_up_runs=10,
    )


def _execution(
    execution_id: str,
    *,
    status: ExecutionStatus,
    task_name: str = "market.kline.sync",
    worker_id: str | None = "worker-1",
    started_at: datetime | None = None,
    created_at: datetime | None = None,
) -> TaskExecution:
    return TaskExecution(
        id=execution_id,
        task_name=task_name,
        schedule_id="schedule-1",
        queue="market-data",
        priority=2,
        status=status,
        scheduled_at=NOW,
        started_at=started_at,
        worker_id=worker_id,
        trace_id=f"trace-{execution_id}",
        created_at=created_at or NOW,
    )


def test_memory_repositories_upsert_filter_and_find_stale() -> None:
    async def scenario() -> None:
        tasks = InMemoryTaskRepository()
        schedules = InMemoryScheduleRepository()
        executions = InMemoryExecutionRepository()

        saved = await tasks.save(_task())
        await tasks.save(_task(description="updated"))
        loaded_tasks = await tasks.list()
        assert saved.name == "market.kline.sync"
        assert len(loaded_tasks) == 1
        assert loaded_tasks[0].description == "updated"
        assert loaded_tasks[0].retry_policy == saved.retry_policy

        saved_schedule = await schedules.save(_schedule())
        loaded_schedule = await schedules.get(saved_schedule.id)
        assert loaded_schedule is not None
        assert isinstance(loaded_schedule.trigger, CronTrigger)
        assert loaded_schedule.to_dict() == saved_schedule.to_dict()
        assert [item.id for item in await schedules.list(enabled=True)] == [
            saved_schedule.id
        ]

        updated_schedule = await schedules.update_state(
            saved_schedule.id,
            last_fire_at=NOW,
            next_fire_at=NOW + timedelta(minutes=5),
            enabled=False,
        )
        assert updated_schedule is not None
        assert updated_schedule.last_fire_at == NOW
        assert updated_schedule.next_fire_at == NOW + timedelta(minutes=5)
        assert updated_schedule.enabled is False
        assert await schedules.list(enabled=True) == []
        assert await schedules.delete(saved_schedule.id) is True
        assert await schedules.delete(saved_schedule.id) is False

        await executions.save(
            _execution(
                "old-running",
                status=ExecutionStatus.RUNNING,
                started_at=NOW - timedelta(minutes=10),
                created_at=NOW - timedelta(minutes=10),
            )
        )
        await executions.save(
            _execution(
                "new-running",
                status=ExecutionStatus.RUNNING,
                started_at=NOW - timedelta(minutes=1),
                created_at=NOW - timedelta(minutes=1),
            )
        )
        await executions.save(
            _execution(
                "failed",
                status=ExecutionStatus.FAILED,
                started_at=NOW - timedelta(minutes=20),
                created_at=NOW - timedelta(minutes=20),
            )
        )
        await executions.save(
            _execution(
                "queued",
                status=ExecutionStatus.QUEUED,
                worker_id=None,
            )
        )

        claimed = await executions.claim(
            "queued",
            worker_id="worker-claim",
            attempt=1,
        )
        assert claimed is not None
        assert claimed.status is ExecutionStatus.RUNNING
        assert claimed.worker_id == "worker-claim"
        assert (
            await executions.claim(
                "queued",
                worker_id="worker-other",
                attempt=1,
            )
            is None
        )

        running = await executions.list(status=ExecutionStatus.RUNNING)
        assert [item.id for item in running] == [
            "queued",
            "new-running",
            "old-running",
        ]
        assert [
            item.id
            for item in await executions.list(
                task_name="market.kline.sync",
                limit=2,
                offset=1,
            )
        ] == ["new-running", "old-running"]
        stale = await executions.find_stale_running(
            NOW - timedelta(minutes=5),
            worker_ids={"worker-1"},
        )
        assert [item.id for item in stale] == ["old-running"]
        assert (
            await executions.find_stale_running(
                NOW,
                worker_ids={"worker-missing"},
            )
            == []
        )
        await executions.append_log(
            "old-running",
            "INFO",
            "TaskStarted",
            "started",
            {"attempt": 1},
        )

    asyncio.run(scenario())


def test_memory_schedule_repository_round_trip_preserves_trigger_type() -> None:
    async def scenario() -> None:
        repository = InMemoryScheduleRepository()
        schedule = _schedule("round-trip")

        await repository.save(schedule)
        restored = await repository.get(schedule.id)

        assert restored is not None
        assert type(restored.trigger) is CronTrigger
        assert restored.to_dict() == schedule.to_dict()

    asyncio.run(scenario())


class FakePostgresStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
        *,
        fetch: str = "none",
    ) -> Any:
        values = dict(params or {})
        self.calls.append((statement, values))
        if fetch == "one" and "UPDATE scheduler.scheduler_execution" in statement:
            return None
        if fetch == "one" and "RETURNING id" in statement:
            return {"id": values.get("schedule_id")}
        return None

    def query(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((statement, dict(params or {})))
        return []

    def query_one(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.calls.append((statement, dict(params or {})))
        return None


def test_postgres_repositories_emit_expected_sql_with_fake_store() -> None:
    async def scenario() -> None:
        store = FakePostgresStore()
        tasks = PostgresTaskRepository(store)  # type: ignore[arg-type]
        schedules = PostgresScheduleRepository(store)  # type: ignore[arg-type]
        executions = PostgresExecutionRepository(store)  # type: ignore[arg-type]
        schedule = _schedule("postgres-schedule")

        ensure_scheduler_schema(store)  # type: ignore[arg-type]
        await tasks.save(_task())
        await tasks.get("market.kline.sync")
        await tasks.list()
        await schedules.save(schedule)
        await schedules.update_state(
            schedule.id,
            last_fire_at=NOW,
            next_fire_at=NOW + timedelta(minutes=5),
            enabled=False,
        )
        assert await schedules.delete(schedule.id) is True
        await executions.save(
            _execution(
                "postgres-execution",
                status=ExecutionStatus.RUNNING,
                started_at=NOW - timedelta(minutes=10),
            )
        )
        await executions.claim(
            "postgres-execution",
            worker_id="worker-1",
            attempt=1,
        )
        await executions.list(
            status=ExecutionStatus.RUNNING,
            task_name="market.kline.sync",
            limit=25,
            offset=5,
        )
        await executions.find_stale_running(
            NOW - timedelta(minutes=5),
            worker_ids={"worker-1", "worker-2"},
        )
        await executions.append_log(
            "postgres-execution",
            "ERROR",
            "TaskFailed",
            "boom",
            {"attempt": 2},
        )

        statements = "\n".join(statement for statement, _params in store.calls)
        assert "CREATE SCHEMA IF NOT EXISTS scheduler" in statements
        assert "CREATE TABLE IF NOT EXISTS scheduler.scheduler_task" in statements
        assert (
            "CREATE TABLE IF NOT EXISTS scheduler.scheduler_schedule"
            in statements
        )
        assert (
            "CREATE TABLE IF NOT EXISTS scheduler.scheduler_execution"
            in statements
        )
        assert (
            "CREATE TABLE IF NOT EXISTS scheduler.scheduler_execution_log"
            in statements
        )
        assert (
            "CREATE INDEX IF NOT EXISTS idx_scheduler_execution_stale_running"
            in statements
        )

        task_insert = _statement(store, "INSERT INTO scheduler.scheduler_task")
        assert "CAST(:retry_policy_json AS jsonb)" in task_insert
        assert "ON CONFLICT (name) DO UPDATE" in task_insert

        schedule_insert = _statement(
            store,
            "INSERT INTO scheduler.scheduler_schedule",
        )
        assert "max_catch_up_runs" in schedule_insert
        assert "CAST(:trigger_config_json AS jsonb)" in schedule_insert
        assert "ON CONFLICT (id) DO UPDATE" in schedule_insert

        execution_insert = _statement(
            store,
            "INSERT INTO scheduler.scheduler_execution",
        )
        assert "CAST(:params_json AS jsonb)" in execution_insert
        assert "ON CONFLICT (id) DO UPDATE" in execution_insert
        execution_claim = _statement(
            store,
            "UPDATE scheduler.scheduler_execution",
        )
        assert "status IN ('PENDING', 'QUEUED', 'WAITING', 'RETRYING')" in execution_claim
        assert "attempt = :attempt" in execution_claim

        execution_list = _statement(
            store,
            "FROM scheduler.scheduler_execution",
            params={"status": "RUNNING"},
        )
        assert "status = :status" in execution_list
        assert "task_name = :task_name" in execution_list
        assert "LIMIT :limit OFFSET :offset" in execution_list

        stale_query = _statement(
            store,
            "COALESCE(started_at, queued_at, scheduled_at, created_at)",
            params={"before": NOW - timedelta(minutes=5)},
        )
        assert "COALESCE(started_at, queued_at, scheduled_at, created_at)" in stale_query
        assert "worker_id IN (:worker_id_0, :worker_id_1)" in stale_query

        log_insert = _statement(
            store,
            "INSERT INTO scheduler.scheduler_execution_log",
        )
        assert "CAST(:data_json AS jsonb)" in log_insert

        stale_params = next(
            params
            for statement, params in store.calls
            if "worker_id IN (:worker_id_0, :worker_id_1)" in statement
        )
        assert stale_params["worker_id_0"] == "worker-1"
        assert stale_params["worker_id_1"] == "worker-2"

    asyncio.run(scenario())


def _statement(
    store: FakePostgresStore,
    marker: str,
    *,
    params: dict[str, Any] | None = None,
) -> str:
    for statement, values in store.calls:
        if marker not in statement:
            continue
        if params is None or all(values.get(key) == value for key, value in params.items()):
            return statement
    raise AssertionError(f"statement not found: {marker}")

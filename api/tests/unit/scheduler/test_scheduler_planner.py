from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from xquant.scheduler.application import (
    PlannerAlreadyRegistered,
    SchedulerService,
    TaskExecutor,
    TaskPlan,
    TaskPlanNode,
    TaskRegistry,
)
from xquant.scheduler.domain import (
    ExecutionStatus,
    TaskContext,
    TaskDefinition,
    TaskExecution,
    TaskResult,
)
from xquant.scheduler.repository import (
    InMemoryExecutionRepository,
    InMemoryScheduleRepository,
    InMemoryTaskRepository,
    PostgresExecutionRepository,
    PostgresTaskRepository,
    ensure_scheduler_schema,
)
from xquant.scheduler.runtime import build_scheduler_runtime
from xquant.scheduler.worker import Worker, WorkerConfig
from xquant.storage import SchedulerSettings, StorageSettings

NOW = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)


class _Handler:
    async def execute(self, context: TaskContext) -> TaskResult:
        return TaskResult(success=True, data={"execution_id": context.execution_id})


class _TwoNodePlanner:
    async def plan(self, execution: TaskExecution) -> TaskPlan:
        return TaskPlan(
            (
                TaskPlanNode(
                    key="first",
                    task_name="child.first",
                    params={"source": execution.id},
                ),
                TaskPlanNode(
                    key="second",
                    task_name="child.second",
                    depends_on=("first",),
                ),
            )
        )


def _definition(
    name: str,
    *,
    planner: str | None = None,
) -> TaskDefinition:
    return TaskDefinition(
        name=name,
        handler="_Handler",
        planner=planner,
    )


def _execution(
    execution_id: str,
    *,
    depends_on: tuple[str, ...] = (),
) -> TaskExecution:
    return TaskExecution(
        id=execution_id,
        task_name="child",
        scheduled_at=NOW,
        depends_on=depends_on,
        trace_id=f"trace-{execution_id}",
    )


def test_task_plan_rejects_duplicate_unknown_and_cyclic_nodes() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        TaskPlan(
            (
                TaskPlanNode(key="a", task_name="task"),
                TaskPlanNode(key="a", task_name="task"),
            )
        )

    with pytest.raises(ValueError, match="unknown"):
        TaskPlan(
            (
                TaskPlanNode(
                    key="a",
                    task_name="task",
                    depends_on=("missing",),
                ),
            )
        )

    with pytest.raises(ValueError, match="cycle"):
        TaskPlan(
            (
                TaskPlanNode(
                    key="a",
                    task_name="task",
                    depends_on=("b",),
                ),
                TaskPlanNode(
                    key="b",
                    task_name="task",
                    depends_on=("a",),
                ),
            )
        )


def test_task_plan_topological_order_is_stable() -> None:
    first = TaskPlanNode(key="first", task_name="task")
    second = TaskPlanNode(
        key="second",
        task_name="task",
        depends_on=("first",),
    )
    third = TaskPlanNode(
        key="third",
        task_name="task",
        depends_on=("first",),
    )

    plan = TaskPlan((third, second, first))

    assert [node.key for node in plan.topological_order()] == [
        "first",
        "third",
        "second",
    ]


def test_local_runtime_executes_dependent_plan_nodes() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        registry.register(
            _definition("plan.root", planner="two-node"),
            _Handler(),
        )
        registry.register(_definition("child.first"), _Handler())
        registry.register(_definition("child.second"), _Handler())
        registry.register_planner("two-node", _TwoNodePlanner())
        settings = StorageSettings(
            storage_backend="legacy_sqlite",
            scheduler=SchedulerSettings(
                enabled=True,
                embedded=True,
                engine_type="memory",
                dispatcher_type="local",
            ),
        )
        runtime = build_scheduler_runtime(
            object(),
            settings,
            registry=registry,
        )
        assert runtime is not None
        runtime.dispatcher.waiting_retry_delay = 0.01
        await runtime.start(
            start_engine=True,
            start_worker=False,
            start_recovery=False,
        )
        try:
            await runtime.service.run_task("plan.root")
            for _ in range(100):
                executions = await runtime.service.execution_repository.list(limit=20)
                children = [
                    item
                    for item in executions
                    if item.task_name in {"child.first", "child.second"}
                ]
                if len(children) == 2 and all(
                    item.status is ExecutionStatus.SUCCESS
                    for item in children
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("local DAG did not finish")
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_task_registry_registers_planner_instances_and_async_callables() -> None:
    class Planner:
        async def plan(self, execution: TaskExecution) -> TaskPlan:
            return TaskPlan((TaskPlanNode(key=execution.id, task_name="task"),))

    async def function_planner(_execution: TaskExecution) -> TaskPlan:
        return TaskPlan()

    registry = TaskRegistry()
    instance = Planner()
    registry.register_planner("instance", instance)
    callable_planner = registry.register_planner("callable", function_planner)

    assert registry.get_planner("instance") is instance
    assert registry.get_planner("callable") is callable_planner
    with pytest.raises(PlannerAlreadyRegistered):
        registry.register_planner("instance", instance)


class RecordingDispatcher:
    def __init__(self, repository: InMemoryExecutionRepository) -> None:
        self.repository = repository
        self.executions: list[TaskExecution] = []
        self.saved_snapshots: list[set[str]] = []

    async def dispatch(self, execution: TaskExecution) -> None:
        self.executions.append(execution)
        saved = await self.repository.list(limit=100)
        self.saved_snapshots.append({item.id for item in saved})


def test_scheduler_service_creates_children_before_dispatching_plan() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        registry.register(_definition("root", planner="fanout"), _Handler())
        registry.register(_definition("child"), _Handler())

        class Planner:
            async def plan(self, execution: TaskExecution) -> TaskPlan:
                assert execution.params == {"symbol": "600000"}
                return TaskPlan(
                    (
                        TaskPlanNode(
                            key="second",
                            task_name="child",
                            params={"step": 2},
                            priority=1,
                            queue="batch",
                            depends_on=("first",),
                        ),
                        TaskPlanNode(
                            key="first",
                            task_name="child",
                            params={"step": 1},
                        ),
                    )
                )

        registry.register_planner("fanout", Planner())
        executions = InMemoryExecutionRepository()
        dispatcher = RecordingDispatcher(executions)
        ids = iter(["root-id", "first-id", "second-id"])
        service = SchedulerService(
            registry,
            InMemoryTaskRepository(),
            InMemoryScheduleRepository(),
            executions,
            dispatcher,  # type: ignore[arg-type]
            now=lambda: NOW,
            id_factory=lambda: next(ids),
        )
        await service.sync_registry()

        root = await service.run_task(
            "root",
            {"symbol": "600000"},
            trace_id="trace-root",
        )

        assert root.id == "root-id"
        assert root.status is ExecutionStatus.SUCCESS
        assert root.result is not None
        assert root.result.message == "created 2 planned executions"
        assert root.result.data is not None
        assert root.result.data["child_execution_ids"] == [
            "first-id",
            "second-id",
        ]
        assert [item.id for item in dispatcher.executions] == [
            "first-id",
            "second-id",
        ]
        assert all({"first-id", "second-id"} <= snapshot for snapshot in dispatcher.saved_snapshots)

        first = await executions.get("first-id")
        second = await executions.get("second-id")
        assert first is not None
        assert second is not None
        assert first.parent_execution_id == root.id
        assert second.parent_execution_id == root.id
        assert first.trace_id == root.trace_id == "trace-root"
        assert second.trace_id == root.trace_id
        assert second.depends_on == (first.id,)
        assert second.queue == "batch"
        assert second.priority == 1
        assert await executions.get(root.id) == root

    asyncio.run(scenario())


class FakeExecutionRepository:
    def __init__(self) -> None:
        self.saved: list[TaskExecution] = []
        self.logs: list[str] = []

    async def save(self, execution: TaskExecution) -> None:
        self.saved.append(execution)

    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.logs.append(event)


def test_task_executor_dependency_success_wait_and_failure_propagation() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        calls: list[str] = []

        async def handler(context: TaskContext) -> TaskResult:
            calls.append(context.execution_id)
            return TaskResult(success=True)

        registry.register(_definition("child"), handler)
        dependencies: dict[str, TaskExecution] = {}

        async def resolver(execution: TaskExecution) -> list[TaskExecution]:
            return [dependencies[dependency_id] for dependency_id in execution.depends_on]

        repository = FakeExecutionRepository()
        executor = TaskExecutor(
            registry,
            repository,
            dependency_resolver=resolver,
        )

        dependencies["ready"] = _execution(
            "ready",
        )
        dependencies["ready"].status = ExecutionStatus.SUCCESS
        ready = await executor.execute(_execution("run", depends_on=("ready",)))
        assert ready.status is ExecutionStatus.SUCCESS
        assert calls == ["run"]

        dependencies["pending"] = _execution("pending")
        dependencies["pending"].status = ExecutionStatus.RUNNING
        waiting = await executor.execute(_execution("wait", depends_on=("pending",)))
        assert waiting.status is ExecutionStatus.WAITING
        assert calls == ["run"]
        assert repository.logs[-1] == "TaskWaiting"

        dependencies["failed"] = _execution("failed")
        dependencies["failed"].status = ExecutionStatus.FAILED
        skipped = await executor.execute(_execution("skip", depends_on=("failed",)))
        assert skipped.status is ExecutionStatus.SKIPPED
        assert calls == ["run"]
        assert repository.logs[-1] == "TaskSkipped"

    asyncio.run(scenario())


class FakeDelivery:
    def __init__(self, execution: TaskExecution) -> None:
        self.execution = execution


class WaitingDispatcher:
    def __init__(self, execution: TaskExecution) -> None:
        self.execution = execution
        self.delivered = False
        self.acked = 0
        self.requeue_delays: list[float] = []
        self.requeued = asyncio.Event()

    async def receive_any(
        self,
        _queues: Any,
        *,
        worker_id: str,
        timeout: float,
    ) -> FakeDelivery | None:
        if self.delivered:
            return None
        self.delivered = True
        return FakeDelivery(self.execution)

    @staticmethod
    def decode_execution(delivery: FakeDelivery) -> TaskExecution:
        return delivery.execution

    async def ack(self, _delivery: FakeDelivery) -> bool:
        self.acked += 1
        return True

    async def requeue(
        self,
        _delivery: FakeDelivery,
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.requeue_delays.append(delay_seconds)
        self.requeued.set()


class WaitingExecutor:
    worker_id: str | None = None

    async def execute(self, execution: TaskExecution) -> TaskExecution:
        execution.status = ExecutionStatus.WAITING
        return execution


def test_worker_requeues_waiting_execution_after_ack() -> None:
    async def scenario() -> None:
        dispatcher = WaitingDispatcher(_execution("waiting-worker"))
        worker = Worker(
            dispatcher,  # type: ignore[arg-type]
            WaitingExecutor(),
            config=WorkerConfig(
                queues=("default",),
                concurrency=1,
                poll_interval=0.01,
            ),
            worker_id="worker-waiting",
        )

        await worker.start()
        await asyncio.wait_for(dispatcher.requeued.wait(), timeout=1)
        await worker.stop()

        assert dispatcher.acked == 1
        assert len(dispatcher.requeue_delays) == 1
        assert dispatcher.requeue_delays[0] >= 1

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
        self.calls.append((statement, dict(params or {})))
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


def test_postgres_repository_persists_planner_and_dependencies() -> None:
    async def scenario() -> None:
        store = FakePostgresStore()
        tasks = PostgresTaskRepository(store)  # type: ignore[arg-type]
        executions = PostgresExecutionRepository(store)  # type: ignore[arg-type]

        ensure_scheduler_schema(store)  # type: ignore[arg-type]
        await tasks.save(_definition("planned", planner="fanout"))
        await executions.save(_execution("child", depends_on=("parent",)))

        statements = "\n".join(statement for statement, _ in store.calls)
        assert "ADD COLUMN IF NOT EXISTS planner" in statements
        assert "ADD COLUMN IF NOT EXISTS depends_on_json" in statements

        task_insert = next(
            statement
            for statement, _ in store.calls
            if "INSERT INTO scheduler.scheduler_task" in statement
        )
        assert "planner" in task_insert
        assert store.calls[-1][1]["depends_on_json"] == '["parent"]'

        execution_insert = next(
            statement
            for statement, _ in store.calls
            if "INSERT INTO scheduler.scheduler_execution" in statement
        )
        assert "CAST(:depends_on_json AS jsonb)" in execution_insert

    asyncio.run(scenario())

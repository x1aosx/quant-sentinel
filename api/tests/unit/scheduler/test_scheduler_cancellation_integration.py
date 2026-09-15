from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from xquant.scheduler.application import (
    SchedulerService,
    TaskExecutor,
    TaskRegistry,
)
from xquant.scheduler.cancellation import MemoryCancellationManager
from xquant.scheduler.domain import (
    ExecutionStatus,
    RetryPolicy,
    TaskDefinition,
    TaskExecution,
    TaskResult,
)
from xquant.scheduler.repository import (
    InMemoryExecutionRepository,
    InMemoryScheduleRepository,
    InMemoryTaskRepository,
)
from xquant.scheduler.runtime import build_scheduler_runtime
from xquant.scheduler.worker import Worker, WorkerConfig
from xquant.storage import SchedulerSettings, StorageSettings

NOW = datetime(2026, 9, 15, 9, 31, tzinfo=UTC)


class _BlockingHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, _context) -> TaskResult:
        self.started.set()
        await self.release.wait()
        return TaskResult(success=True)


class _CountingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, execution: TaskExecution) -> TaskExecution:
        self.calls += 1
        return execution


class _Delivery:
    def __init__(self, execution: TaskExecution) -> None:
        self.execution = execution


class _Dispatcher:
    def __init__(self, execution: TaskExecution) -> None:
        self.execution = execution
        self.acked = 0
        self.requeued = 0

    async def receive_any(self, queues, *, worker_id: str, timeout: float):
        return _Delivery(self.execution)

    @staticmethod
    def decode_execution(delivery: _Delivery) -> TaskExecution:
        return delivery.execution

    async def ack(self, _delivery: _Delivery) -> bool:
        self.acked += 1
        return True

    async def requeue(self, _delivery: _Delivery, *, delay_seconds: float = 0) -> None:
        self.requeued += 1


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.executions: list[TaskExecution] = []

    async def dispatch(self, execution: TaskExecution) -> None:
        self.executions.append(execution)


def _execution(
    *,
    task_name: str = "blocking",
    execution_id: str = "cancel-running",
) -> TaskExecution:
    return TaskExecution(
        id=execution_id,
        task_name=task_name,
        scheduled_at=NOW,
        status=ExecutionStatus.RUNNING,
        trace_id="trace-cancel",
    )


def test_executor_polling_cancels_running_handler() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        handler = _BlockingHandler()
        registry.register(
            TaskDefinition(name="blocking", handler="_BlockingHandler"),
            handler,
        )
        repository = InMemoryExecutionRepository()
        cancellation = MemoryCancellationManager()
        executor = TaskExecutor(
            registry,
            repository,
            cancellation_manager=cancellation,
            cancellation_poll_interval=0.01,
        )
        execution = _execution()
        task = asyncio.create_task(executor.execute(execution))
        await asyncio.wait_for(handler.started.wait(), timeout=1)

        await cancellation.request_cancel(execution.id, "operator request")
        with pytest.raises(asyncio.CancelledError):
            await task

        stored = await repository.get(execution.id)
        assert stored is not None
        assert stored.status is ExecutionStatus.CANCELLED
        assert stored.error_message == "operator request"

    asyncio.run(scenario())


def test_executor_interrupts_retry_backoff_when_cancelled() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        calls = 0

        class FlakyHandler:
            async def execute(self, _context) -> TaskResult:
                nonlocal calls
                calls += 1
                raise RuntimeError("first attempt failed")

        registry.register(
            TaskDefinition(
                name="flaky",
                handler="FlakyHandler",
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    strategy="fixed",
                    initial_delay_seconds=0.3,
                    jitter=False,
                ),
            ),
            FlakyHandler(),
        )
        repository = InMemoryExecutionRepository()
        cancellation = MemoryCancellationManager()
        executor = TaskExecutor(
            registry,
            repository,
            cancellation_manager=cancellation,
            cancellation_poll_interval=0.01,
        )
        execution = _execution(task_name="flaky", execution_id="cancel-retry")
        task = asyncio.create_task(executor.execute(execution))
        for _ in range(50):
            if calls == 1:
                break
            await asyncio.sleep(0.01)

        await cancellation.request_cancel(execution.id, "cancel retry")
        with pytest.raises(asyncio.CancelledError):
            await task

        assert calls == 1
        stored = await repository.get(execution.id)
        assert stored is not None
        assert stored.status is ExecutionStatus.CANCELLED

    asyncio.run(scenario())


def test_worker_acks_queued_cancellation_without_executing() -> None:
    async def scenario() -> None:
        repository = InMemoryExecutionRepository()
        cancellation = MemoryCancellationManager()
        execution = _execution()
        await repository.save(execution)
        await cancellation.request_cancel(execution.id, "cancelled before claim")
        dispatcher = _Dispatcher(execution)
        runner = _CountingExecutor()
        worker = Worker(
            dispatcher,  # type: ignore[arg-type]
            runner,  # type: ignore[arg-type]
            config=WorkerConfig(queues=("default",), concurrency=1),
            repository=repository,
            cancellation_manager=cancellation,
            worker_id="worker-cancel",
        )

        await worker._run_delivery(_Delivery(execution))

        assert runner.calls == 0
        assert dispatcher.acked == 1
        assert not await cancellation.is_cancelled(execution.id)
        stored = await repository.get(execution.id)
        assert stored is not None
        assert stored.status is ExecutionStatus.CANCELLED

    asyncio.run(scenario())


def test_worker_acks_replace_cancellation_instead_of_requeueing() -> None:
    async def scenario() -> None:
        class ReplacedExecutor:
            async def execute(self, execution: TaskExecution) -> TaskExecution:
                execution.status = ExecutionStatus.CANCELLED
                raise asyncio.CancelledError

        execution = _execution()
        dispatcher = _Dispatcher(execution)
        worker = Worker(
            dispatcher,  # type: ignore[arg-type]
            ReplacedExecutor(),  # type: ignore[arg-type]
            config=WorkerConfig(queues=("default",), concurrency=1),
            worker_id="worker-replace",
        )

        await worker._run_delivery(_Delivery(execution))

        assert dispatcher.acked == 1
        assert dispatcher.requeued == 0

    asyncio.run(scenario())


def test_scheduler_service_cancels_queued_execution() -> None:
    async def scenario() -> None:
        registry = TaskRegistry()
        registry.register(
            TaskDefinition(name="queued", handler="_BlockingHandler"),
            _BlockingHandler(),
        )
        execution_repository = InMemoryExecutionRepository()
        cancellation = MemoryCancellationManager()
        dispatcher = _RecordingDispatcher()
        service = SchedulerService(
            registry,
            InMemoryTaskRepository(),
            InMemoryScheduleRepository(),
            execution_repository,
            dispatcher,  # type: ignore[arg-type]
            cancellation_manager=cancellation,
        )
        await service.sync_registry()
        execution = await service.run_task("queued")

        cancelled = await service.cancel_execution(
            execution.id,
            reason="operator request",
        )

        assert cancelled.status is ExecutionStatus.CANCELLED
        assert await cancellation.is_cancelled(execution.id)

    asyncio.run(scenario())


def test_local_runtime_wires_cancellation_and_dependency_resolution() -> None:
    settings = StorageSettings(
        storage_backend="legacy_sqlite",
        scheduler=SchedulerSettings(
            enabled=True,
            embedded=True,
            engine_type="memory",
            dispatcher_type="local",
        ),
    )

    runtime = build_scheduler_runtime(object(), settings)

    assert runtime is not None
    assert runtime.executor.dependency_resolver is not None
    assert runtime.executor.rate_limiter is not None
    assert runtime.executor.cancellation_manager is (
        runtime.service.cancellation_manager
    )

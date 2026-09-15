from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from xquant.scheduler.application import (
    TaskAlreadyRegistered,
    TaskExecutor,
    TaskRegistry,
)
from xquant.scheduler.dispatcher import LocalDispatcher
from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    ExecutionStatus,
    RetryPolicy,
    TaskContext,
    TaskDefinition,
    TaskExecution,
    TaskResult,
)
from xquant.scheduler.lock import MemoryLockManager
from xquant.scheduler.rate_limit import MemoryRateLimiter


class FakeRepository:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []
        self.logs: list[dict[str, Any]] = []

    async def save(self, execution: TaskExecution) -> None:
        self.saved.append(
            {
                "id": execution.id,
                "status": execution.status,
                "attempt": execution.attempt,
                "error_type": execution.error_type,
            }
        )

    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.logs.append(
            {
                "execution_id": execution_id,
                "level": level,
                "event": event,
                "message": message,
                "data": data or {},
            }
        )


class Handler:
    def __init__(self, function) -> None:
        self.function = function

    async def execute(self, context: TaskContext) -> TaskResult:
        return await self.function(context)


def _definition(
    name: str,
    *,
    timeout_seconds: int = 5,
    retry_policy: RetryPolicy | None = None,
    concurrency_policy: ConcurrencyPolicy = ConcurrencyPolicy.ALLOW,
) -> TaskDefinition:
    return TaskDefinition(
        name=name,
        handler="Handler",
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy,
        concurrency_policy=concurrency_policy,
    )


def _execution(
    task_name: str,
    *,
    execution_id: str = "execution-1",
) -> TaskExecution:
    return TaskExecution(
        id=execution_id,
        task_name=task_name,
        scheduled_at=datetime(2026, 9, 15, 9, 30, tzinfo=UTC),
        trace_id=f"trace-{execution_id}",
    )


def test_task_registry_supports_explicit_and_decorator_registration() -> None:
    registry = TaskRegistry()
    definition = _definition("explicit")
    handler = Handler(lambda _context: TaskResult(success=True))
    registry.register(definition, handler)

    assert registry.get_definition("explicit") is definition
    assert registry.get_handler("explicit") is handler
    assert registry.list() == [definition]
    with pytest.raises(TaskAlreadyRegistered):
        registry.register(definition, handler)

    @registry.task(name="decorated", timeout_seconds=7)
    class DecoratedHandler:
        async def execute(self, context: TaskContext) -> TaskResult:
            return TaskResult(success=True, message=context.task_name)

    decorated_definition = registry.get_definition("decorated")
    assert decorated_definition.handler == "DecoratedHandler"
    assert decorated_definition.timeout_seconds == 7
    assert isinstance(registry.get_handler("decorated"), DecoratedHandler)


def test_task_executor_success_lifecycle() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        registry = TaskRegistry()
        contexts: list[TaskContext] = []

        async def handler(context: TaskContext) -> TaskResult:
            contexts.append(context)
            return TaskResult(success=True, message="ok", metrics={"rows": 3})

        registry.register(_definition("success"), Handler(handler))
        executor = TaskExecutor(registry, repository, worker_id="worker-1")
        execution = await executor.execute(_execution("success"))

        assert execution.status is ExecutionStatus.SUCCESS
        assert execution.result == TaskResult(
            success=True,
            message="ok",
            metrics={"rows": 3},
        )
        assert contexts[0].attempt == 1
        assert contexts[0].worker_id == "worker-1"
        assert execution.duration_ms is not None
        assert [item["status"] for item in repository.saved] == [
            ExecutionStatus.RUNNING,
            ExecutionStatus.SUCCESS,
        ]
        assert [item["event"] for item in repository.logs] == [
            "TaskStarted",
            "TaskSucceeded",
        ]

    asyncio.run(scenario())


def test_task_executor_retries_exceptions_to_failure() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        registry = TaskRegistry()
        attempts = 0

        async def handler(_context: TaskContext) -> TaskResult:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("boom")

        registry.register(
            _definition(
                "retry",
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    initial_delay_seconds=0,
                    jitter=False,
                ),
            ),
            Handler(handler),
        )
        executor = TaskExecutor(registry, repository)
        execution = await executor.execute(_execution("retry"))

        assert attempts == 3
        assert execution.attempt == 3
        assert execution.max_attempts == 3
        assert execution.status is ExecutionStatus.FAILED
        assert execution.error_type == "RuntimeError"
        assert execution.error_message == "boom"
        assert [item["event"] for item in repository.logs].count("TaskRetrying") == 2
        assert repository.logs[-1]["event"] == "TaskFailed"

    asyncio.run(scenario())


def test_task_executor_timeout_records_timeout_status() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        registry = TaskRegistry()

        async def handler(_context: TaskContext) -> TaskResult:
            await asyncio.sleep(1)
            return TaskResult(success=True)

        registry.register(
            _definition("timeout", timeout_seconds=1),
            Handler(handler),
        )
        executor = TaskExecutor(registry, repository)
        execution = await executor.execute(_execution("timeout"))

        assert execution.status is ExecutionStatus.TIMEOUT
        assert execution.error_type == "TimeoutError"
        assert repository.logs[-1]["event"] == "TaskTimedOut"

    asyncio.run(scenario())


def test_task_executor_forbid_skips_concurrent_execution() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        registry = TaskRegistry()
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(_context: TaskContext) -> TaskResult:
            started.set()
            await release.wait()
            return TaskResult(success=True)

        registry.register(
            _definition("forbid", concurrency_policy=ConcurrencyPolicy.FORBID),
            Handler(handler),
        )
        executor = TaskExecutor(registry, repository)
        first = asyncio.create_task(executor.execute(_execution("forbid", execution_id="1")))
        await started.wait()

        second_execution = await executor.execute(
            _execution("forbid", execution_id="2")
        )

        assert second_execution.status is ExecutionStatus.SKIPPED
        release.set()
        await first
        assert repository.logs[-1]["event"] == "TaskSucceeded"

    asyncio.run(scenario())


def test_task_executor_cancellation_is_not_swallowed() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        registry = TaskRegistry()
        started = asyncio.Event()

        async def handler(_context: TaskContext) -> TaskResult:
            started.set()
            await asyncio.sleep(30)
            return TaskResult(success=True)

        registry.register(_definition("cancel"), Handler(handler))
        executor = TaskExecutor(registry, repository)
        execution = _execution("cancel")
        task = asyncio.create_task(executor.execute(execution))
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert execution.status is ExecutionStatus.CANCELLED
        assert repository.logs[-1]["event"] == "TaskCancelled"

    asyncio.run(scenario())


def test_local_dispatcher_runs_and_shuts_down_tasks() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        registry = TaskRegistry()

        async def handler(_context: TaskContext) -> TaskResult:
            await asyncio.sleep(0)
            return TaskResult(success=True)

        registry.register(_definition("dispatch"), Handler(handler))
        executor = TaskExecutor(registry, repository)
        dispatcher = LocalDispatcher(executor)
        execution = _execution("dispatch")

        await dispatcher.dispatch(execution)
        await dispatcher.wait_all()
        await dispatcher.shutdown()

        assert execution.status is ExecutionStatus.SUCCESS
        assert dispatcher.running_count == 0

    asyncio.run(scenario())


def test_memory_lock_validates_owner_token() -> None:
    async def scenario() -> None:
        manager = MemoryLockManager()

        assert await manager.acquire("provider:tushare", 30, "owner-1")
        assert not await manager.acquire("provider:tushare", 30, "owner-2")
        assert not await manager.release("provider:tushare", "owner-2")
        assert await manager.acquire("provider:tushare", 30, "owner-2") is False
        assert await manager.release("provider:tushare", "owner-1")
        assert await manager.acquire("provider:tushare", 30, "owner-2")

    asyncio.run(scenario())


def test_memory_rate_limiter_waits_for_refill() -> None:
    async def scenario() -> None:
        current = 0.0

        async def sleep(seconds: float) -> None:
            nonlocal current
            current += seconds

        limiter = MemoryRateLimiter(
            rate=2,
            period=1,
            capacity=2,
            clock=lambda: current,
            sleep=sleep,
        )

        await limiter.acquire("provider:test")
        await limiter.acquire("provider:test")
        await limiter.acquire("provider:test")

        assert current == pytest.approx(0.5)

    asyncio.run(scenario())

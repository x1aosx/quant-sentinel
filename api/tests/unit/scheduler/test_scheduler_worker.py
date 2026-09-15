from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from xquant.scheduler.application.recovery_service import (
    RecoveryAction,
    RecoveryService,
)
from xquant.scheduler.dispatcher.redis import RedisDispatcher
from xquant.scheduler.domain import (
    ExecutionStatus,
    RetryPolicy,
    TaskDefinition,
    TaskExecution,
)
from xquant.scheduler.repository import (
    InMemoryExecutionRepository,
    InMemoryTaskRepository,
)
from xquant.scheduler.worker import Worker, WorkerConfig, WorkerHeartbeat

NOW = datetime(2026, 9, 15, 9, 30, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class FakeAsyncRedis:
    """Small in-memory subset used by RedisDispatcher and heartbeat tests."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.lists: dict[str, list[str]] = {}
        self.values: dict[str, tuple[str, int | None]] = {}
        self.counters: dict[str, int] = {}

    async def zadd(self, key: str, mapping: Mapping[str, float]) -> int:
        target = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in target:
                added += 1
            target[member] = float(score)
        return added

    async def zpopmin(self, key: str, count: int = 1) -> list[tuple[str, float]]:
        target = self.zsets.get(key, {})
        selected = sorted(target.items(), key=lambda item: (item[1], item[0]))[:count]
        for member, _ in selected:
            target.pop(member, None)
        return selected

    async def zrange(
        self,
        key: str,
        start: int,
        end: int,
        *,
        withscores: bool = False,
    ) -> list[str] | list[tuple[str, float]]:
        selected = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        bounded = selected[start : end + 1] if end >= 0 else selected[start:]
        if withscores:
            return [(member, score) for member, score in bounded]
        return [member for member, _ in bounded]

    async def zrem(self, key: str, *members: str) -> int:
        target = self.zsets.get(key, {})
        removed = 0
        for member in members:
            if member in target:
                target.pop(member)
                removed += 1
        return removed

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def rpush(self, key: str, *values: str) -> int:
        target = self.lists.setdefault(key, [])
        target.extend(values)
        return len(target)

    async def lpop(self, key: str) -> str | None:
        target = self.lists.get(key, [])
        return target.pop(0) if target else None

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        keys = args[:numkeys]
        argv = args[numkeys:]
        if "PROMOTE_AND_CLAIM" in script:
            delayed_key, ready_key, sequence_key, inflight_key = keys
            now, limit = float(argv[0]), int(argv[1])
            due = [
                member
                for member, score in sorted(
                    self.zsets.get(delayed_key, {}).items(),
                    key=lambda item: (item[1], item[0]),
                )
                if score <= now
            ][:limit]
            for member in due:
                self.zsets[delayed_key].pop(member, None)
                payload = json.loads(member)
                sequence = await self.incr(sequence_key)
                score = int(payload["priority"]) * 1_000_000_000_000 + sequence
                await self.zadd(ready_key, {member: score})
            popped = await self.zpopmin(ready_key, 1)
            if not popped:
                return None
            member, _ = popped[0]
            await self.zadd(inflight_key, {member: float(argv[3])})
            return member
        if "RECOVER_EXPIRED" in script:
            ready_key, inflight_key, sequence_key = keys
            now, limit = float(argv[0]), int(argv[1])
            expired = [
                member
                for member, score in sorted(
                    self.zsets.get(inflight_key, {}).items(),
                    key=lambda item: (item[1], item[0]),
                )
                if score <= now
            ][:limit]
            recovered = 0
            for member in expired:
                self.zsets[inflight_key].pop(member, None)
                payload = json.loads(member)
                sequence = await self.incr(sequence_key)
                score = int(payload["priority"]) * 1_000_000_000_000 + sequence
                await self.zadd(ready_key, {member: score})
                recovered += 1
            return recovered
        raise AssertionError(f"unexpected script: {script[:80]}")

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        xx: bool = False,
    ) -> bool:
        if xx and key not in self.values:
            return False
        self.values[key] = (value, ex)
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.values:
                self.values.pop(key)
                removed += 1
            self.zsets.pop(key, None)
            self.lists.pop(key, None)
        return removed

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [key for key in self.values if key.startswith(prefix)]


def _execution(
    execution_id: str,
    *,
    priority: int = 5,
    scheduled_at: datetime = NOW,
    attempt: int = 1,
    max_attempts: int = 1,
    status: ExecutionStatus = ExecutionStatus.PENDING,
    worker_id: str | None = None,
) -> TaskExecution:
    return TaskExecution(
        id=execution_id,
        task_name="market.sync",
        scheduled_at=scheduled_at,
        queue="market-data",
        priority=priority,
        status=status,
        attempt=attempt,
        max_attempts=max_attempts,
        worker_id=worker_id,
        trace_id=f"trace-{execution_id}",
        started_at=scheduled_at if status is ExecutionStatus.RUNNING else None,
    )


def test_redis_dispatcher_priority_delay_ack_and_lease_recovery() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        client = FakeAsyncRedis()
        dispatcher = RedisDispatcher(
            client,
            lease_seconds=30,
            now=clock,
        )
        await dispatcher.dispatch(_execution("low", priority=8))
        await dispatcher.dispatch(_execution("high", priority=1))

        first = await dispatcher.receive(
            "market-data",
            worker_id="worker-1",
            timeout=0,
        )
        assert first is not None
        assert dispatcher.decode_execution(first).id == "high"
        assert json.loads(first.message)["queue"] == "market-data"
        assert await dispatcher.ack(first)

        low = await dispatcher.receive(
            "market-data",
            worker_id="worker-1",
            timeout=0,
        )
        assert low is not None
        assert dispatcher.decode_execution(low).id == "low"
        assert await dispatcher.ack(low)

        await dispatcher.dispatch(
            _execution(
                "delayed",
                scheduled_at=clock() + timedelta(seconds=10),
            )
        )
        assert (
            await dispatcher.receive(
                "market-data",
                worker_id="worker-1",
                timeout=0,
            )
            is None
        )
        clock.advance(11)
        delayed = await dispatcher.receive(
            "market-data",
            worker_id="worker-1",
            timeout=0,
        )
        assert delayed is not None
        assert dispatcher.decode_execution(delayed).id == "delayed"
        assert await dispatcher.recover("market-data", now=clock()) == 0
        clock.advance(31)
        assert await dispatcher.recover("market-data", now=clock()) == 1
        redelivered = await dispatcher.receive(
            "market-data",
            worker_id="worker-2",
            timeout=0,
        )
        assert redelivered is not None
        assert dispatcher.decode_execution(redelivered).id == "delayed"
        assert await dispatcher.ack(redelivered)

    asyncio.run(scenario())


class FakeDelivery:
    def __init__(self, execution: TaskExecution) -> None:
        self.execution = execution


class FakeDispatcher:
    def __init__(self, deliveries: list[TaskExecution]) -> None:
        self.deliveries = list(deliveries)
        self.acked = 0
        self.requeued = 0
        self.ack_event = asyncio.Event()

    async def receive_any(
        self,
        queues,
        *,
        worker_id: str,
        timeout: float,
    ) -> FakeDelivery | None:
        if self.deliveries:
            return FakeDelivery(self.deliveries.pop(0))
        return None

    @staticmethod
    def decode_execution(delivery: FakeDelivery) -> TaskExecution:
        return delivery.execution

    async def ack(self, delivery: FakeDelivery) -> bool:
        self.acked += 1
        self.ack_event.set()
        return True

    async def requeue(
        self,
        delivery: FakeDelivery,
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.requeued += 1


class FakeExecutor:
    def __init__(self, *, wait_event: asyncio.Event | None = None) -> None:
        self.started = asyncio.Event()
        self.wait_event = wait_event
        self.executed: list[TaskExecution] = []
        self.worker_id: str | None = None

    async def execute(self, execution: TaskExecution) -> TaskExecution:
        self.executed.append(execution)
        self.started.set()
        if self.wait_event is not None:
            await self.wait_event.wait()
        return replace(execution, status=ExecutionStatus.SUCCESS)


def test_worker_consumes_execution_before_ack_and_waits_on_graceful_stop() -> None:
    async def scenario() -> None:
        execution = _execution("worker-delivery")
        dispatcher = FakeDispatcher([execution])
        release = asyncio.Event()
        executor = FakeExecutor(wait_event=release)
        worker = Worker(
            dispatcher,
            executor,
            config=WorkerConfig(
                queues=("market-data",),
                concurrency=1,
                poll_interval=0.01,
                graceful_shutdown_timeout=1,
            ),
            worker_id="worker-test",
        )

        await worker.start()
        await asyncio.wait_for(executor.started.wait(), timeout=1)
        assert dispatcher.acked == 0
        release.set()
        await asyncio.wait_for(dispatcher.ack_event.wait(), timeout=1)
        await worker.stop()

        assert [item.id for item in executor.executed] == ["worker-delivery"]
        assert executor.executed[0].worker_id == "worker-test"
        assert dispatcher.acked == 1
        assert dispatcher.requeued == 0

    asyncio.run(scenario())


def test_worker_cancels_and_requeues_when_graceful_timeout_is_exceeded() -> None:
    async def scenario() -> None:
        dispatcher = FakeDispatcher([_execution("slow-delivery")])
        executor = FakeExecutor(wait_event=asyncio.Event())
        worker = Worker(
            dispatcher,
            executor,
            config=WorkerConfig(
                queues=("market-data",),
                concurrency=1,
                poll_interval=0.01,
                graceful_shutdown_timeout=0.01,
            ),
            worker_id="worker-cancel",
        )

        await worker.start()
        await asyncio.wait_for(executor.started.wait(), timeout=1)
        await worker.stop()

        assert dispatcher.acked == 0
        assert dispatcher.requeued == 1

    asyncio.run(scenario())


def test_worker_heartbeat_register_renew_and_unregister() -> None:
    async def scenario() -> None:
        client = FakeAsyncRedis()
        heartbeat = WorkerHeartbeat(
            client,
            worker_id="worker-heartbeat",
            queue_prefix="test:scheduler",
            ttl_seconds=30,
            interval_seconds=5,
            now=lambda: NOW,
        )

        await heartbeat.register(queues=("market-data",), status="running")
        assert await heartbeat.is_alive("worker-heartbeat")
        assert await heartbeat.renew()
        assert await heartbeat.live_worker_ids() == {"worker-heartbeat"}
        await heartbeat.unregister()
        assert not await heartbeat.is_alive("worker-heartbeat")

    asyncio.run(scenario())


class RecordingDispatcher:
    def __init__(self) -> None:
        self.executions: list[TaskExecution] = []

    async def dispatch(self, execution: TaskExecution) -> None:
        self.executions.append(execution)


class QueueRecoveryDispatcher(RecordingDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_calls: list[tuple[tuple[str, ...], int]] = []

    async def recover_all(
        self,
        queues: tuple[str, ...],
        *,
        now: datetime,
        limit: int,
    ) -> int:
        self.recovery_calls.append((queues, limit))
        return 2


def test_recovery_recovers_expired_redis_deliveries() -> None:
    async def scenario() -> None:
        dispatcher = QueueRecoveryDispatcher()
        service = RecoveryService(
            InMemoryExecutionRepository(),
            dispatcher=dispatcher,
            queues=("market-data", "batch"),
            batch_size=7,
            now=lambda: NOW,
        )

        report = await service.run_once(now=NOW)

        assert report.queue_recovered == 2
        assert dispatcher.recovery_calls == [(("market-data", "batch"), 7)]

    asyncio.run(scenario())


def test_recovery_retries_stale_running_until_policy_is_exhausted() -> None:
    async def scenario() -> None:
        executions = InMemoryExecutionRepository()
        tasks = InMemoryTaskRepository()
        dispatcher = RecordingDispatcher()
        definition = TaskDefinition(
            name="market.sync",
            handler="MarketSyncHandler",
            retry_policy=RetryPolicy(
                max_attempts=3,
                strategy="fixed",
                initial_delay_seconds=5,
                jitter=False,
            ),
        )
        await tasks.save(definition)
        stale = _execution(
            "stale-retry",
            scheduled_at=NOW - timedelta(minutes=2),
            attempt=1,
            max_attempts=3,
            status=ExecutionStatus.RUNNING,
            worker_id="dead-worker",
        )
        await executions.save(stale)
        service = RecoveryService(
            executions,
            dispatcher=dispatcher,
            task_repository=tasks,
            stale_after_seconds=60,
            now=lambda: NOW,
        )

        report = await service.run_once(now=NOW)

        assert report.retried == 1
        assert report.failed == 0
        recovered = await executions.get("stale-retry")
        assert recovered is not None
        assert recovered.status is ExecutionStatus.RETRYING
        assert recovered.attempt == 2
        assert recovered.scheduled_at == NOW + timedelta(seconds=5)
        assert [item.id for item in dispatcher.executions] == ["stale-retry"]

        await executions.save(
            replace(
                _execution(
                    "stale-failed",
                    scheduled_at=NOW - timedelta(minutes=2),
                    attempt=3,
                    max_attempts=3,
                    status=ExecutionStatus.RUNNING,
                    worker_id="dead-worker",
                ),
                task_name="market.sync",
            )
        )
        failed_report = await service.run_once(now=NOW)
        assert failed_report.failed == 1
        failed = await executions.get("stale-failed")
        assert failed is not None
        assert failed.status is ExecutionStatus.FAILED

    asyncio.run(scenario())


def test_recovery_respects_explicit_task_policy() -> None:
    async def scenario() -> None:
        executions = InMemoryExecutionRepository()
        dispatcher = RecordingDispatcher()
        stale = _execution(
            "stale-policy",
            scheduled_at=NOW - timedelta(minutes=2),
            status=ExecutionStatus.RUNNING,
            worker_id="dead-worker",
        )
        await executions.save(stale)
        service = RecoveryService(
            executions,
            dispatcher=dispatcher,
            stale_after_seconds=60,
            policy_resolver=lambda execution, definition: RecoveryAction.REQUEUE,
            now=lambda: NOW,
        )

        report = await service.run_once(now=NOW)

        assert report.requeued == 1
        assert report.failed == 0
        assert [item.id for item in dispatcher.executions] == ["stale-policy"]

    asyncio.run(scenario())

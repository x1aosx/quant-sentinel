from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from xquant.scheduler.domain import (
    ExecutionStatus,
    RetryPolicy,
    TaskDefinition,
    TaskExecution,
)

logger = logging.getLogger(__name__)


class RecoveryAction(StrEnum):
    RETRY = "RETRY"
    REQUEUE = "REQUEUE"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Summary of one stale-execution scan."""

    scanned: int = 0
    retried: int = 0
    requeued: int = 0
    failed: int = 0
    queue_recovered: int = 0
    errors: tuple[str, ...] = ()

    @property
    def changed(self) -> int:
        return self.retried + self.requeued + self.failed


class ExecutionRepository(Protocol):
    async def save(self, execution: TaskExecution) -> Any: ...

    async def find_stale_running(
        self,
        before: datetime,
        worker_ids: set[str] | None = None,
    ) -> list[TaskExecution]: ...

    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> Any: ...


class TaskPolicyRepository(Protocol):
    async def get(self, name: str) -> TaskDefinition | None: ...


class LiveWorkers(Protocol):
    async def live_worker_ids(self) -> set[str]: ...


class Dispatcher(Protocol):
    async def dispatch(self, execution: TaskExecution) -> None: ...


PolicyResolver = Callable[
    [TaskExecution, TaskDefinition | None],
    RecoveryAction | str | tuple[RecoveryAction | str, RetryPolicy | None],
]


class RecoveryService:
    """Return stale RUNNING executions to the queue or close them as failed."""

    def __init__(
        self,
        repository: ExecutionRepository,
        *,
        dispatcher: Dispatcher | None = None,
        task_repository: TaskPolicyRepository | None = None,
        heartbeat: LiveWorkers | None = None,
        queues: Sequence[str] | None = None,
        interval_seconds: float = 30.0,
        stale_after_seconds: float = 60.0,
        batch_size: int = 100,
        policy_resolver: PolicyResolver | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.repository = repository
        self.dispatcher = dispatcher
        self.task_repository = task_repository
        self.heartbeat = heartbeat
        self.queues = tuple(dict.fromkeys(queues or ()))
        self.interval_seconds = interval_seconds
        self.stale_after_seconds = stale_after_seconds
        self.batch_size = batch_size
        self.policy_resolver = policy_resolver
        self._now = now or (lambda: datetime.now(UTC))
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="scheduler-recovery",
        )
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        task = self._task
        self._task = None
        self._started = False
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def run_once(self, *, now: datetime | None = None) -> RecoveryReport:
        current = now or self._now()
        before = current - timedelta(seconds=self.stale_after_seconds)
        queue_recovered = 0
        errors: list[str] = []
        recover_all = getattr(self.dispatcher, "recover_all", None)
        if self.queues and callable(recover_all):
            try:
                queue_recovered = int(
                    await _maybe_await(
                        recover_all(
                            self.queues,
                            now=current,
                            limit=self.batch_size,
                        )
                    )
                    or 0
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"queue-recovery:{type(exc).__name__}:{exc or 'recovery failed'}"
                )
        stale = await self.repository.find_stale_running(before)
        stale = await self._filter_live_workers(stale)
        stale = stale[: self.batch_size]

        retried = 0
        requeued = 0
        failed = 0
        for execution in stale:
            try:
                action = await self._action_for(execution)
                if action in (RecoveryAction.RETRY, RecoveryAction.REQUEUE):
                    requeued_now = await self._retry_or_requeue(
                        execution,
                        action=action,
                        now=current,
                    )
                    if requeued_now:
                        if action is RecoveryAction.RETRY:
                            retried += 1
                        else:
                            requeued += 1
                        continue
                await self._fail(execution, now=current)
                failed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{execution.id}:{type(exc).__name__}:{exc or 'recovery failed'}")

        return RecoveryReport(
            scanned=len(stale),
            retried=retried,
            requeued=requeued,
            failed=failed,
            queue_recovered=queue_recovered,
            errors=tuple(errors),
        )

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                # The repository boundary may be temporarily unavailable. The
                # next scan retries without terminating the service loop.
                logger.exception("scheduler recovery scan failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                continue

    async def _action_for(self, execution: TaskExecution) -> RecoveryAction:
        definition = None
        if self.task_repository is not None:
            definition = await _maybe_await(self.task_repository.get(execution.task_name))
        if self.policy_resolver is not None:
            resolved = await _maybe_await(self.policy_resolver(execution, definition))
            if isinstance(resolved, tuple):
                return RecoveryAction(resolved[0])
            return RecoveryAction(resolved)
        max_attempts = _max_attempts(execution, definition)
        return RecoveryAction.RETRY if execution.attempt < max_attempts else RecoveryAction.FAIL

    async def _retry_or_requeue(
        self,
        execution: TaskExecution,
        *,
        action: RecoveryAction,
        now: datetime,
    ) -> bool:
        definition = None
        if self.task_repository is not None:
            definition = await _maybe_await(self.task_repository.get(execution.task_name))
        max_attempts = _max_attempts(execution, definition)
        if action is RecoveryAction.RETRY and execution.attempt >= max_attempts:
            return False
        retry_policy = _retry_policy(execution, definition)
        delay = retry_policy.delay_for(execution.attempt) if retry_policy else 0.0
        scheduled_at = now + timedelta(seconds=max(0.0, delay))
        updated = replace(
            execution,
            status=ExecutionStatus.RETRYING,
            attempt=execution.attempt + 1,
            scheduled_at=scheduled_at,
            queued_at=None,
            started_at=None,
            finished_at=None,
            worker_id=None,
            error_type="StaleExecution",
            error_message="worker heartbeat expired or execution timed out",
        )
        saved = await self.repository.save(updated)
        if isinstance(saved, TaskExecution):
            updated = saved
        append_log = getattr(self.repository, "append_log", None)
        if callable(append_log):
            await _maybe_await(
                append_log(
                    execution.id,
                    "WARNING",
                    "TaskRecovered",
                    "stale execution returned to the queue",
                    {
                        "action": action.value,
                        "attempt": updated.attempt,
                        "delay_seconds": delay,
                    },
                )
            )
        if self.dispatcher is not None:
            await _maybe_await(self.dispatcher.dispatch(updated))
        return True

    async def _fail(self, execution: TaskExecution, *, now: datetime) -> None:
        updated = replace(
            execution,
            status=ExecutionStatus.FAILED,
            finished_at=now,
            error_type="StaleExecution",
            error_message="worker heartbeat expired or execution timed out",
        )
        saved = await self.repository.save(updated)
        if isinstance(saved, TaskExecution):
            updated = saved
        append_log = getattr(self.repository, "append_log", None)
        if callable(append_log):
            await _maybe_await(
                append_log(
                    execution.id,
                    "ERROR",
                    "TaskFailed",
                    "stale execution exhausted its retry policy",
                    {"attempt": updated.attempt},
                )
            )

    async def _filter_live_workers(
        self,
        executions: list[TaskExecution],
    ) -> list[TaskExecution]:
        if self.heartbeat is None:
            return executions
        live_worker_ids = await _maybe_await(self.heartbeat.live_worker_ids())
        if not live_worker_ids:
            return executions
        return [
            execution
            for execution in executions
            if execution.worker_id is None or execution.worker_id not in live_worker_ids
        ]


def _max_attempts(
    execution: TaskExecution,
    definition: TaskDefinition | None,
) -> int:
    retry_policy = _retry_policy(execution, definition)
    if retry_policy is not None:
        return retry_policy.max_attempts
    return max(execution.max_attempts, execution.attempt)


def _retry_policy(
    execution: TaskExecution,
    definition: TaskDefinition | None,
) -> RetryPolicy | None:
    if definition is not None and definition.retry_policy is not None:
        return definition.retry_policy
    return None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "RecoveryAction",
    "RecoveryReport",
    "RecoveryService",
]

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    ExecutionStatus,
    RetryPolicy,
    TaskContext,
    TaskDefinition,
    TaskEvent,
    TaskExecution,
    TaskResult,
)

from ..cancellation import CancellationManager
from .task_registry import TaskHandler, TaskRegistry

logger = logging.getLogger(__name__)


@runtime_checkable
class ExecutionRepository(Protocol):
    async def save(self, execution: TaskExecution) -> Any:
        ...

    async def append_log(
        self,
        execution_id: str,
        level: str,
        event: str,
        message: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Any:
        ...


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(
        self,
        execution: TaskExecution,
    ) -> Sequence[TaskExecution] | Mapping[str, TaskExecution]:
        ...


@runtime_checkable
class TaskEventListener(Protocol):
    async def handle(self, event: TaskEvent) -> Any:
        ...


class TaskExecutor:
    """Runs a task handler and owns its complete execution lifecycle."""

    def __init__(
        self,
        registry: TaskRegistry,
        repository: ExecutionRepository,
        *,
        lock_manager: Any | None = None,
        rate_limiter: Any | None = None,
        dependency_resolver: DependencyResolver
        | Callable[[TaskExecution], Any]
        | None = None,
        cancellation_manager: CancellationManager | None = None,
        cancellation_poll_interval: float = 0.25,
        event_listener: TaskEventListener
        | Callable[[TaskEvent], Any]
        | None = None,
        worker_id: str | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.lock_manager = lock_manager
        self.rate_limiter = rate_limiter
        self.dependency_resolver = dependency_resolver
        self.cancellation_manager = cancellation_manager
        self.cancellation_poll_interval = cancellation_poll_interval
        self.event_listener = event_listener
        self.worker_id = worker_id
        self._now = now or (lambda: datetime.now(UTC))
        self._active_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._serial_locks: dict[str, asyncio.Lock] = {}
        self._state_guard = asyncio.Lock()

    async def execute(self, execution: TaskExecution) -> TaskExecution:
        if not execution.trace_id:
            execution.trace_id = uuid.uuid4().hex

        try:
            definition = self.registry.get_definition(execution.task_name)
            handler = self.registry.get_handler(execution.task_name)
        except Exception as exc:  # noqa: BLE001 - registry failures belong to the execution
            await self._finish_exception(execution, exc, status=ExecutionStatus.FAILED)
            return execution

        if not definition.enabled:
            await self._skip(execution, "task is disabled")
            return execution

        if (
            self.cancellation_manager is not None
            and await self.cancellation_manager.is_cancelled(execution.id)
        ):
            await self._handle_cancelled(execution)
            return execution

        try:
            dependency_status, dependency_message = await self._dependency_gate(
                execution
            )
        except Exception as exc:  # noqa: BLE001 - resolver failures are terminal
            await self._finish_exception(
                execution,
                exc,
                status=ExecutionStatus.FAILED,
            )
            return execution
        if dependency_status is ExecutionStatus.WAITING:
            await self._wait(
                execution,
                dependency_message or "dependencies are not ready",
            )
            return execution
        if dependency_status is ExecutionStatus.SKIPPED:
            await self._skip(
                execution,
                dependency_message or "dependencies did not succeed",
            )
            return execution

        policy = ConcurrencyPolicy(definition.concurrency_policy)
        serial_lock: asyncio.Lock | None = None
        activated = False
        lock_acquired = False
        lock_key = f"scheduler:lock:{execution.task_name}"
        lock_owner = f"{execution.id}:{uuid.uuid4().hex}"

        try:
            if policy is ConcurrencyPolicy.SERIAL:
                serial_lock = await self._get_serial_lock(execution.task_name)
                await serial_lock.acquire()
                activated = True
                self._activate_current_task(execution.task_name)
            elif policy is ConcurrencyPolicy.FORBID:
                activated = await self._try_activate_current_task(execution.task_name)
                if not activated:
                    await self._skip(execution, "another execution is already running")
                    return execution
            elif policy is ConcurrencyPolicy.REPLACE:
                await self._replace_active_tasks(execution.task_name)
                self._activate_current_task(execution.task_name)
                activated = True
            else:
                self._activate_current_task(execution.task_name)
                activated = True

            if policy is not ConcurrencyPolicy.ALLOW and self.lock_manager is not None:
                max_attempts = (
                    definition.retry_policy.max_attempts
                    if definition.retry_policy is not None
                    else 1
                )
                retry_budget = (
                    definition.retry_policy.max_delay_seconds
                    * max(0, max_attempts - 1)
                    if definition.retry_policy is not None
                    else 0
                )
                lock_ttl = int(
                    definition.timeout_seconds * max_attempts
                    + retry_budget
                    + 30
                )
                lock_acquired = bool(
                    await self._maybe_await(
                        self.lock_manager.acquire(
                            lock_key,
                            lock_ttl,
                            lock_owner,
                        )
                    )
                )
                if not lock_acquired:
                    await self._skip(execution, "distributed lock is already held")
                    return execution

            if definition.rate_limit_key and self.rate_limiter is not None:
                await self._maybe_await(
                    self.rate_limiter.acquire(definition.rate_limit_key)
                )

            await self._start(execution)
            await self._run_attempts(execution, definition, handler)
            return execution
        except asyncio.CancelledError:
            await self._handle_cancelled(execution)
            raise
        finally:
            if lock_acquired and self.lock_manager is not None:
                try:
                    await self._maybe_await(
                        self.lock_manager.release(lock_key, lock_owner)
                    )
                except Exception:
                    logger.exception("failed to release scheduler lock for %s", execution.id)
            if activated:
                self._deactivate_current_task(execution.task_name)
            if serial_lock is not None and serial_lock.locked():
                serial_lock.release()

    async def _run_attempts(
        self,
        execution: TaskExecution,
        definition: TaskDefinition,
        handler: TaskHandler,
    ) -> None:
        retry_policy = definition.retry_policy
        max_attempts = retry_policy.max_attempts if retry_policy else 1
        execution.max_attempts = max_attempts
        attempt = max(execution.attempt, 1)

        while True:
            execution.attempt = attempt
            execution.error_type = None
            execution.error_message = None
            execution.result = None
            if (
                self.cancellation_manager is not None
                and await self.cancellation_manager.is_cancelled(execution.id)
            ):
                raise asyncio.CancelledError(
                    f"execution {execution.id} was cancelled"
                )
            if attempt > 1:
                await self._start(execution)
            started_at = self._now()
            context = TaskContext(
                task_name=execution.task_name,
                execution_id=execution.id,
                schedule_id=execution.schedule_id,
                scheduled_at=execution.scheduled_at,
                started_at=started_at,
                attempt=attempt,
                params=dict(execution.params),
                trace_id=execution.trace_id,
                worker_id=self.worker_id,
                cancellation_token=None,
            )

            failure: tuple[str, str] | None = None
            result: TaskResult | None = None
            timed_out = False

            try:
                returned = await self._execute_handler(
                    execution,
                    handler,
                    context,
                    timeout_seconds=definition.timeout_seconds,
                )
                if not isinstance(returned, TaskResult):
                    raise TypeError(
                        "task handler must return xquant.scheduler.domain.TaskResult"
                    )
                result = returned
                execution.result = result
                if result.success:
                    await self._finish(
                        execution,
                        ExecutionStatus.SUCCESS,
                        event_type="TaskSucceeded",
                        message=result.message,
                    )
                    return
                failure = (
                    "TaskResultFailure",
                    result.message or "task returned an unsuccessful result",
                )
            except TimeoutError:
                timed_out = True
                failure = (
                    "TimeoutError",
                    f"task exceeded timeout of {definition.timeout_seconds} seconds",
                )
            except Exception as exc:  # noqa: BLE001 - handler failures are recorded
                failure = (type(exc).__name__, str(exc) or type(exc).__name__)

            if failure is None:
                failure = ("TaskExecutionError", "task failed without an error")

            error_type, error_message = failure
            execution.error_type = error_type
            execution.error_message = error_message

            if self._should_retry(retry_policy, attempt, error_type):
                execution.status = ExecutionStatus.RETRYING
                execution.finished_at = None
                await self._save(execution)
                delay = retry_policy.delay_for(attempt) if retry_policy else 0.0
                await self._emit(
                    execution,
                    "TaskRetrying",
                    message=error_message,
                    data={
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "delay_seconds": delay,
                        "error_type": error_type,
                    },
                )
                await self._wait_for_retry_delay(execution.id, delay)
                attempt += 1
                continue

            status = ExecutionStatus.TIMEOUT if timed_out else ExecutionStatus.FAILED
            event_type = "TaskTimedOut" if timed_out else "TaskFailed"
            await self._finish(
                execution,
                status,
                event_type=event_type,
                message=error_message,
            )
            return

    async def _wait_for_retry_delay(
        self,
        execution_id: str,
        delay_seconds: float,
    ) -> None:
        if delay_seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay_seconds
        while True:
            if (
                self.cancellation_manager is not None
                and await self.cancellation_manager.is_cancelled(execution_id)
            ):
                raise asyncio.CancelledError(
                    f"execution {execution_id} was cancelled"
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(
                min(self.cancellation_poll_interval, remaining)
            )

    async def _execute_handler(
        self,
        execution: TaskExecution,
        handler: TaskHandler,
        context: TaskContext,
        *,
        timeout_seconds: float,
    ) -> TaskResult:
        handler_task = asyncio.create_task(handler.execute(context))
        cancellation_task: asyncio.Task[bool] | None = None
        if self.cancellation_manager is not None:
            cancellation_task = asyncio.create_task(
                self._wait_for_cancellation(execution.id),
                name=f"scheduler-cancellation:{execution.id}",
            )

        try:
            if cancellation_task is None:
                return await asyncio.wait_for(
                    handler_task,
                    timeout=timeout_seconds,
                )

            done, _ = await asyncio.wait(
                {handler_task, cancellation_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation_task in done and cancellation_task.result():
                handler_task.cancel()
                await asyncio.gather(handler_task, return_exceptions=True)
                raise asyncio.CancelledError(
                    f"execution {execution.id} was cancelled"
                )
            if handler_task in done:
                return handler_task.result()
            raise TimeoutError
        finally:
            if not handler_task.done():
                handler_task.cancel()
            if cancellation_task is not None and not cancellation_task.done():
                cancellation_task.cancel()
            await asyncio.gather(
                *[
                    task
                    for task in (handler_task, cancellation_task)
                    if task is not None
                ],
                return_exceptions=True,
            )

    async def _wait_for_cancellation(self, execution_id: str) -> bool:
        if self.cancellation_manager is None:
            return False
        while True:
            if await self.cancellation_manager.is_cancelled(execution_id):
                return True
            await asyncio.sleep(self.cancellation_poll_interval)

    async def _start(self, execution: TaskExecution) -> None:
        started_at = self._now()
        if execution.started_at is None:
            execution.started_at = started_at
        execution.worker_id = self.worker_id
        execution.status = ExecutionStatus.RUNNING
        execution.finished_at = None
        await self._save(execution)
        await self._emit(execution, "TaskStarted", data={"attempt": execution.attempt})

    async def _finish(
        self,
        execution: TaskExecution,
        status: ExecutionStatus,
        *,
        event_type: str,
        message: str | None = None,
    ) -> None:
        finished_at = self._now()
        execution.status = status
        execution.finished_at = finished_at
        if execution.started_at is not None:
            execution.duration_ms = max(
                0.0,
                (finished_at - execution.started_at).total_seconds() * 1000,
            )
        await self._save(execution)
        await self._emit(
            execution,
            event_type,
            message=message,
            data={
                "attempt": execution.attempt,
                "error_type": execution.error_type,
            },
        )

    async def _finish_exception(
        self,
        execution: TaskExecution,
        exc: Exception,
        *,
        status: ExecutionStatus,
    ) -> None:
        execution.error_type = type(exc).__name__
        execution.error_message = str(exc) or type(exc).__name__
        await self._finish(
            execution,
            status,
            event_type="TaskFailed",
            message=execution.error_message,
        )

    async def _skip(self, execution: TaskExecution, reason: str) -> None:
        if execution.status is ExecutionStatus.SKIPPED:
            return
        execution.status = ExecutionStatus.SKIPPED
        execution.finished_at = self._now()
        execution.result = TaskResult(success=False, message=reason)
        await self._save(execution)
        await self._emit(
            execution,
            "TaskSkipped",
            message=reason,
            data={"reason": reason},
        )

    async def _wait(self, execution: TaskExecution, reason: str) -> None:
        execution.status = ExecutionStatus.WAITING
        execution.finished_at = None
        execution.updated_at = self._now()
        await self._save(execution)
        await self._emit(
            execution,
            "TaskWaiting",
            message=reason,
            data={"depends_on": list(execution.depends_on)},
        )

    async def _dependency_gate(
        self,
        execution: TaskExecution,
    ) -> tuple[ExecutionStatus | None, str | None]:
        if not execution.depends_on:
            return None, None
        if self.dependency_resolver is None:
            return (
                ExecutionStatus.WAITING,
                "dependency resolver is not configured",
            )

        resolver = getattr(self.dependency_resolver, "resolve", None)
        if not callable(resolver):
            resolver = getattr(
                self.dependency_resolver,
                "resolve_dependencies",
                None,
            )
        if not callable(resolver):
            resolver = self.dependency_resolver
        if not callable(resolver):
            raise TypeError("dependency resolver must be callable")

        resolved = await self._maybe_await(resolver(execution))
        dependencies = self._normalize_dependencies(resolved)
        missing = [
            dependency_id
            for dependency_id in execution.depends_on
            if dependency_id not in dependencies
        ]
        if missing:
            return (
                ExecutionStatus.SKIPPED,
                "dependencies were not found: " + ", ".join(missing),
            )

        failed: list[str] = []
        waiting: list[str] = []
        for dependency_id in execution.depends_on:
            dependency = dependencies[dependency_id]
            status = ExecutionStatus(dependency.status)
            if status is ExecutionStatus.SUCCESS:
                continue
            if status in {
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMEOUT,
                ExecutionStatus.CANCELLED,
                ExecutionStatus.SKIPPED,
            }:
                failed.append(f"{dependency_id} ({status.value})")
            else:
                waiting.append(f"{dependency_id} ({status.value})")

        if failed:
            return (
                ExecutionStatus.SKIPPED,
                "dependencies did not succeed: " + ", ".join(failed),
            )
        if waiting:
            return (
                ExecutionStatus.WAITING,
                "waiting for dependencies: " + ", ".join(waiting),
            )
        return None, None

    @staticmethod
    def _normalize_dependencies(
        resolved: Any,
    ) -> dict[str, TaskExecution]:
        if isinstance(resolved, TaskExecution):
            values = (resolved,)
        elif isinstance(resolved, Mapping):
            values = tuple(resolved.values())
        else:
            try:
                values = tuple(resolved)
            except TypeError as exc:
                raise TypeError(
                    "dependency resolver must return TaskExecution values"
                ) from exc

        dependencies: dict[str, TaskExecution] = {}
        for dependency in values:
            if not isinstance(dependency, TaskExecution):
                raise TypeError(
                    "dependency resolver returned a non-TaskExecution value"
                )
            dependencies[dependency.id] = dependency
        return dependencies

    async def _handle_cancelled(self, execution: TaskExecution) -> None:
        if execution.status is ExecutionStatus.CANCELLED:
            return
        try:
            finished_at = self._now()
            execution.status = ExecutionStatus.CANCELLED
            execution.finished_at = finished_at
            execution.error_type = "CancelledError"
            reason = None
            if self.cancellation_manager is not None:
                reason = await self.cancellation_manager.get_reason(execution.id)
            execution.error_message = reason or "task execution was cancelled"
            if execution.started_at is not None:
                execution.duration_ms = max(
                    0.0,
                    (finished_at - execution.started_at).total_seconds() * 1000,
                )
            await self._save(execution)
            await self._emit(
                execution,
                "TaskCancelled",
                message=execution.error_message,
                data={"attempt": execution.attempt},
            )
        except Exception:
            logger.exception("failed to persist cancellation for %s", execution.id)

    async def _save(self, execution: TaskExecution) -> None:
        await self._maybe_await(self.repository.save(execution))

    async def _emit(
        self,
        execution: TaskExecution,
        event_type: str,
        *,
        message: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        event_data = dict(data or {})
        event = TaskEvent(
            event_type=event_type,
            execution_id=execution.id,
            task_name=execution.task_name,
            occurred_at=self._now(),
            trace_id=execution.trace_id,
            data=event_data,
        )
        if self.event_listener is not None:
            listener = getattr(self.event_listener, "handle", self.event_listener)
            try:
                await self._maybe_await(listener(event))
            except Exception:
                logger.exception(
                    "scheduler event listener failed for %s",
                    execution.id,
                )
        try:
            await self._append_log(event, message)
        except Exception:
            logger.exception(
                "failed to append scheduler execution log for %s",
                execution.id,
            )

    async def _append_log(self, event: TaskEvent, message: str | None) -> None:
        append_log = getattr(self.repository, "append_log", None)
        if not callable(append_log):
            return

        message = message or ""
        data = dict(event.data)
        log_message = message or event.event_type
        args: tuple[Any, ...]
        kwargs: dict[str, Any]
        candidates: list[tuple[tuple[Any, ...], dict[str, Any]]] = [
            (
                (),
                {
                    "execution_id": event.execution_id,
                    "level": "INFO",
                    "event": event.event_type,
                    "message": log_message,
                    "data": data,
                },
            ),
            (
                (
                    event.execution_id,
                    "INFO",
                    event.event_type,
                    log_message,
                    data,
                ),
                {},
            ),
            ((event.execution_id, event.event_type, log_message, data), {}),
            ((event.execution_id, event.event_type, log_message), {}),
            ((event.execution_id, event.event_type), {}),
        ]
        try:
            signature = inspect.signature(append_log)
        except (TypeError, ValueError):
            args, kwargs = candidates[0]
        else:
            for candidate_args, candidate_kwargs in candidates:
                try:
                    signature.bind(*candidate_args, **candidate_kwargs)
                except TypeError:
                    continue
                args, kwargs = candidate_args, candidate_kwargs
                break
            else:
                args, kwargs = candidates[0]

        await self._maybe_await(append_log(*args, **kwargs))

    def _should_retry(
        self,
        policy: RetryPolicy | None,
        failed_attempt: int,
        error_type: str,
    ) -> bool:
        if policy is None or not policy.can_retry(failed_attempt):
            return False
        if policy.no_retry_on and _matches_error(error_type, policy.no_retry_on):
            return False
        if policy.retry_on is None:
            return True
        return _matches_error(error_type, policy.retry_on)

    async def _get_serial_lock(self, task_name: str) -> asyncio.Lock:
        async with self._state_guard:
            return self._serial_locks.setdefault(task_name, asyncio.Lock())

    async def _try_activate_current_task(self, task_name: str) -> bool:
        current = asyncio.current_task()
        if current is None:
            return False
        async with self._state_guard:
            active = self._active_tasks.setdefault(task_name, set())
            if active:
                return False
            active.add(current)
            return True

    def _activate_current_task(self, task_name: str) -> None:
        current = asyncio.current_task()
        if current is not None:
            self._active_tasks.setdefault(task_name, set()).add(current)

    def _deactivate_current_task(self, task_name: str) -> None:
        current = asyncio.current_task()
        if current is None:
            return
        active = self._active_tasks.get(task_name)
        if not active:
            return
        active.discard(current)
        if not active:
            self._active_tasks.pop(task_name, None)

    async def _replace_active_tasks(self, task_name: str) -> None:
        current = asyncio.current_task()
        async with self._state_guard:
            replaced = tuple(self._active_tasks.pop(task_name, set()))
            if current is not None:
                self._active_tasks[task_name] = {current}
        for task in replaced:
            if task is not current:
                task.cancel()
        if replaced:
            await asyncio.gather(*replaced, return_exceptions=True)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value


def _matches_error(error_type: str, configured: Sequence[str]) -> bool:
    short_name = error_type.rsplit(".", 1)[-1]
    return any(
        pattern == error_type
        or pattern == short_name
        or pattern.endswith(f".{short_name}")
        for pattern in configured
    )


__all__ = [
    "DependencyResolver",
    "ExecutionRepository",
    "TaskEventListener",
    "TaskExecutor",
]

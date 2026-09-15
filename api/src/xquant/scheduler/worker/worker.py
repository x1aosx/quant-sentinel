from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import socket
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from xquant.scheduler.domain import TaskExecution

from .heartbeat import HeartbeatStore

logger = logging.getLogger(__name__)


class QueueConsumer(Protocol):
    async def receive_any(
        self,
        queues: Sequence[str],
        *,
        worker_id: str,
        timeout: float,
    ) -> Any: ...

    async def ack(self, delivery: Any) -> Any: ...


class ExecutionRunner(Protocol):
    async def execute(self, execution: TaskExecution) -> TaskExecution: ...


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Runtime limits for one scheduler worker."""

    queues: tuple[str, ...] = ("default",)
    concurrency: int = 1
    poll_interval: float = 1.0
    graceful_shutdown_timeout: float = 60.0
    retry_requeue_delay: float = 0.0

    def __post_init__(self) -> None:
        if not self.queues or any(not queue for queue in self.queues):
            raise ValueError("at least one non-empty queue is required")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if self.graceful_shutdown_timeout < 0:
            raise ValueError("graceful_shutdown_timeout cannot be negative")
        if self.retry_requeue_delay < 0:
            raise ValueError("retry_requeue_delay cannot be negative")


class Worker:
    """Consume Redis deliveries while preventing executor re-entry."""

    def __init__(
        self,
        dispatcher: QueueConsumer,
        executor: ExecutionRunner,
        *,
        config: WorkerConfig | None = None,
        queues: Sequence[str] | None = None,
        concurrency: int | None = None,
        poll_interval: float | None = None,
        graceful_shutdown_timeout: float | None = None,
        heartbeat: HeartbeatStore | None = None,
        repository: Any | None = None,
        worker_id: str | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if config is None:
            config = WorkerConfig(
                queues=tuple(queues or ("default",)),
                concurrency=1 if concurrency is None else concurrency,
                poll_interval=1.0 if poll_interval is None else poll_interval,
                graceful_shutdown_timeout=(
                    60.0 if graceful_shutdown_timeout is None else graceful_shutdown_timeout
                ),
            )
        self.config = config
        self.dispatcher = dispatcher
        self.executor = executor
        self.heartbeat = heartbeat
        self.repository = repository or getattr(executor, "repository", None)
        self.worker_id = worker_id or _default_worker_id()
        self._now = now or (lambda: datetime.now(UTC))
        self._stop_event = asyncio.Event()
        self._consumer_tasks: set[asyncio.Task[None]] = set()
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._running_count = 0
        self._started = False

    @property
    def running_count(self) -> int:
        return self._running_count

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    async def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        self._bind_executor()
        if self.heartbeat is not None:
            await self.heartbeat.register(
                queues=self.config.queues,
                running_tasks=0,
                status="running",
            )
            start = getattr(self.heartbeat, "start", None)
            if callable(start):
                await _maybe_await(start(queues=self.config.queues))
        self._consumer_tasks = {
            asyncio.create_task(
                self._consume_loop(),
                name=f"scheduler-worker:{self.worker_id}:{index}",
            )
            for index in range(self.config.concurrency)
        }
        self._started = True

    async def stop(
        self,
        *,
        graceful_timeout: float | None = None,
        cancel: bool = False,
    ) -> None:
        if not self._started:
            return
        self._stop_event.set()
        timeout = (
            self.config.graceful_shutdown_timeout if graceful_timeout is None else graceful_timeout
        )
        tasks = tuple(self._consumer_tasks)
        if cancel:
            for task in tasks:
                task.cancel()
        elif tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._consumer_tasks.clear()
        self._running_tasks.clear()
        if self.heartbeat is not None:
            stop = getattr(self.heartbeat, "stop", None)
            if callable(stop):
                await _maybe_await(stop())
            else:
                await self.heartbeat.unregister()
        self._started = False

    async def run_forever(self, *, install_signal_handlers: bool = True) -> None:
        await self.start()
        restore_handlers: list[tuple[signal.Signals, Any]] = []
        if install_signal_handlers:
            restore_handlers = self._install_signal_handlers()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()
            self._restore_signal_handlers(restore_handlers)

    def request_stop(self) -> None:
        self._stop_event.set()

    async def _consume_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                delivery = await self.dispatcher.receive_any(
                    self.config.queues,
                    worker_id=self.worker_id,
                    timeout=self.config.poll_interval,
                )
                if delivery is None:
                    await self._wait_for_stop(self.config.poll_interval)
                    continue
                task = asyncio.create_task(
                    self._run_delivery(delivery),
                    name=f"scheduler-delivery:{self.worker_id}",
                )
                self._running_tasks.add(task)
                task.add_done_callback(self._running_tasks.discard)
                await task
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduler worker consumer loop failed")
                await self._wait_for_stop(self.config.poll_interval)

    async def _run_delivery(self, delivery: Any) -> None:
        execution: TaskExecution | None = None
        self._set_running_count(self._running_count + 1)
        try:
            execution = self._decode_delivery(delivery)
            execution = replace(execution, worker_id=self.worker_id)
            if self.repository is not None:
                await _maybe_await(self.repository.save(execution))
            result = await self.executor.execute(execution)
            completed = result if isinstance(result, TaskExecution) else execution
            if completed.worker_id is None:
                completed = replace(completed, worker_id=self.worker_id)
            if self.repository is not None:
                await _maybe_await(self.repository.save(completed))
            await self.dispatcher.ack(delivery)
        except asyncio.CancelledError:
            await self._requeue(delivery, execution)
            raise
        except Exception:
            logger.exception("scheduler delivery failed before ack")
            await self._requeue(delivery, execution)
        finally:
            self._set_running_count(max(0, self._running_count - 1))

    async def _requeue(
        self,
        delivery: Any,
        execution: TaskExecution | None,
    ) -> None:
        requeue = getattr(self.dispatcher, "requeue", None)
        if callable(requeue):
            try:
                await _maybe_await(
                    requeue(
                        delivery,
                        delay_seconds=self.config.retry_requeue_delay,
                    )
                )
                await self._save_requeue_state(execution)
                return
            except Exception:
                logger.exception("failed to requeue scheduler delivery")
        if execution is not None:
            await self._save_requeue_state(execution)

    async def _save_requeue_state(
        self,
        execution: TaskExecution | None,
    ) -> None:
        if execution is None or self.repository is None:
            return
        from xquant.scheduler.domain import ExecutionStatus

        status = (
            ExecutionStatus.RETRYING
            if execution.attempt < execution.max_attempts
            else ExecutionStatus.FAILED
        )
        updated = replace(
            execution,
            status=status,
            finished_at=self._now() if status is ExecutionStatus.FAILED else None,
            error_type="WorkerDispatchError",
            error_message="worker could not complete delivery",
        )
        await _maybe_await(self.repository.save(updated))

    def _decode_delivery(self, delivery: Any) -> TaskExecution:
        decode = getattr(self.dispatcher, "decode_execution", None)
        if callable(decode):
            return decode(delivery)
        execution = getattr(delivery, "execution", None)
        if isinstance(execution, TaskExecution):
            return execution
        message = getattr(delivery, "message", delivery)
        if isinstance(message, TaskExecution):
            return message
        raise TypeError("dispatcher must expose decode_execution(delivery)")

    def _bind_executor(self) -> None:
        if getattr(self.executor, "worker_id", self.worker_id) is None:
            try:
                self.executor.worker_id = self.worker_id
            except (AttributeError, TypeError):
                pass

    def _set_running_count(self, running_tasks: int) -> None:
        self._running_count = running_tasks
        if self.heartbeat is not None:
            self.heartbeat.set_running_tasks(running_tasks)

    async def _wait_for_stop(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except TimeoutError:
            return

    def _install_signal_handlers(self) -> list[tuple[signal.Signals, Any]]:
        loop = asyncio.get_running_loop()
        previous: list[tuple[signal.Signals, Any]] = []
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, self.request_stop)
            except (NotImplementedError, RuntimeError):
                continue
            previous.append((signum, None))
        return previous

    def _restore_signal_handlers(
        self,
        previous: list[tuple[signal.Signals, Any]],
    ) -> None:
        loop = asyncio.get_running_loop()
        for signum, _ in previous:
            try:
                loop.remove_signal_handler(signum)
            except (NotImplementedError, RuntimeError):
                continue


SchedulerWorker = Worker
TaskWorker = Worker


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "ExecutionRunner",
    "QueueConsumer",
    "SchedulerWorker",
    "TaskWorker",
    "Worker",
    "WorkerConfig",
]

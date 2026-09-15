from __future__ import annotations

import asyncio

from xquant.scheduler.application.task_executor import TaskExecutor
from xquant.scheduler.domain import ExecutionStatus, TaskExecution

from .base import TaskDispatcher


class LocalDispatcher(TaskDispatcher):
    """Dispatch executions as asyncio tasks in the current process."""

    def __init__(
        self,
        executor: TaskExecutor,
        *,
        waiting_retry_delay: float = 1.0,
    ) -> None:
        if waiting_retry_delay <= 0:
            raise ValueError("waiting_retry_delay must be positive")
        self.executor = executor
        self.waiting_retry_delay = waiting_retry_delay
        self._tasks: set[asyncio.Task[TaskExecution]] = set()
        self._closed = False

    async def dispatch(self, execution: TaskExecution) -> None:
        if self._closed:
            raise RuntimeError("dispatcher is shut down")
        task = asyncio.create_task(
            self._execute_and_reschedule(execution),
            name=f"scheduler:{execution.task_name}:{execution.id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute_and_reschedule(
        self,
        execution: TaskExecution,
    ) -> None:
        result = await self.executor.execute(execution)
        if result.status is not ExecutionStatus.WAITING:
            return
        await asyncio.sleep(self.waiting_retry_delay)
        if not self._closed:
            await self.dispatch(result)

    async def wait_all(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def shutdown(
        self,
        *,
        wait: bool = True,
        cancel: bool = False,
        timeout_seconds: float | None = None,
    ) -> None:
        self._closed = True
        tasks = tuple(self._tasks)
        if cancel:
            for task in tasks:
                task.cancel()
        if wait and tasks:
            if timeout_seconds is not None:
                _, pending = await asyncio.wait(
                    tasks,
                    timeout=timeout_seconds,
                )
                for task in pending:
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def running_count(self) -> int:
        return len(self._tasks)

    @property
    def tasks(self) -> frozenset[asyncio.Task[TaskExecution]]:
        return frozenset(self._tasks)


__all__ = ["LocalDispatcher"]

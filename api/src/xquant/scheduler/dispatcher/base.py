from __future__ import annotations

from abc import ABC, abstractmethod

from xquant.scheduler.domain import TaskExecution


class TaskDispatcher(ABC):
    """Abstract hand-off boundary between scheduling and execution."""

    @abstractmethod
    async def dispatch(self, execution: TaskExecution) -> None:
        ...

    async def shutdown(self, *, wait: bool = True, cancel: bool = False) -> None:
        return None


__all__ = ["TaskDispatcher"]

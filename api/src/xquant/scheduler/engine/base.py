from __future__ import annotations

from abc import ABC, abstractmethod

from xquant.scheduler.domain import ScheduleDefinition


class SchedulerEngine(ABC):
    """Lifecycle boundary for trigger evaluation and schedule management."""

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        ...

    @abstractmethod
    async def add_schedule(self, schedule: ScheduleDefinition) -> None:
        ...

    @abstractmethod
    async def remove_schedule(self, schedule_id: str) -> None:
        ...

    @abstractmethod
    async def pause_schedule(self, schedule_id: str) -> None:
        ...

    @abstractmethod
    async def resume_schedule(self, schedule_id: str) -> None:
        ...

    @abstractmethod
    async def reload(self) -> None:
        ...


__all__ = ["SchedulerEngine"]

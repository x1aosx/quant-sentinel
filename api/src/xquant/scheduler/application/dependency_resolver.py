from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xquant.scheduler.domain import TaskExecution


class ExecutionDependencyResolver:
    """Resolve direct DAG dependencies from the execution repository."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    async def resolve(
        self,
        execution: TaskExecution,
    ) -> Mapping[str, TaskExecution]:
        resolved: dict[str, TaskExecution] = {}
        for dependency_id in execution.depends_on:
            dependency = await self.repository.get(dependency_id)
            if dependency is not None:
                resolved[dependency_id] = dependency
        return resolved


__all__ = ["ExecutionDependencyResolver"]

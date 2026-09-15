from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from xquant.scheduler.domain import TaskExecution


@runtime_checkable
class TaskPlanner(Protocol):
    """Build a dependency graph for one root task execution."""

    async def plan(self, execution: TaskExecution) -> TaskPlan: ...


@dataclass(frozen=True, slots=True)
class TaskPlanNode:
    """One task node in a plan."""

    key: str
    task_name: str
    params: Mapping[str, Any] = field(default_factory=dict)
    priority: int | None = None
    queue: str | None = None
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("plan node key cannot be empty")
        if not self.task_name.strip():
            raise ValueError("plan node task_name cannot be empty")
        if self.priority is not None and not 0 <= self.priority <= 9:
            raise ValueError("plan node priority must be between 0 and 9")
        if self.queue is not None and not self.queue.strip():
            raise ValueError("plan node queue cannot be empty")

        dependencies = tuple(self.depends_on)
        if any(not dependency.strip() for dependency in dependencies):
            raise ValueError("plan node dependencies cannot be empty")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError(f"plan node {self.key!r} has duplicate dependencies")
        if self.key in dependencies:
            raise ValueError(f"plan node {self.key!r} cannot depend on itself")

        object.__setattr__(self, "params", dict(self.params))
        object.__setattr__(self, "depends_on", dependencies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "task_name": self.task_name,
            "params": dict(self.params),
            "priority": self.priority,
            "queue": self.queue,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class TaskPlan:
    """A validated acyclic task graph."""

    nodes: tuple[TaskPlanNode, ...] = ()

    def __post_init__(self) -> None:
        nodes = tuple(self.nodes)
        by_key: dict[str, TaskPlanNode] = {}
        for node in nodes:
            if node.key in by_key:
                raise ValueError(f"duplicate plan node key: {node.key}")
            by_key[node.key] = node

        for node in nodes:
            unknown = [dependency for dependency in node.depends_on if dependency not in by_key]
            if unknown:
                raise ValueError(
                    f"plan node {node.key!r} has unknown dependencies: " + ", ".join(unknown)
                )

        object.__setattr__(self, "nodes", nodes)
        self._validate_acyclic(by_key)

    def topological_order(self) -> tuple[TaskPlanNode, ...]:
        by_key = {node.key: node for node in self.nodes}
        indegree = {node.key: len(node.depends_on) for node in self.nodes}
        dependents: dict[str, list[str]] = {node.key: [] for node in self.nodes}
        for node in self.nodes:
            for dependency in node.depends_on:
                dependents[dependency].append(node.key)

        remaining = [node.key for node in self.nodes]
        ordered: list[TaskPlanNode] = []
        while remaining:
            ready_index = next(
                (index for index, key in enumerate(remaining) if indegree[key] == 0),
                None,
            )
            if ready_index is None:
                raise ValueError("plan contains a dependency cycle")
            key = remaining.pop(ready_index)
            ordered.append(by_key[key])
            for dependent in dependents[key]:
                indegree[dependent] -= 1
        return tuple(ordered)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": [node.to_dict() for node in self.nodes]}

    def _validate_acyclic(
        self,
        by_key: Mapping[str, TaskPlanNode],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visited:
                return
            if key in visiting:
                raise ValueError(f"plan contains a dependency cycle at {key!r}")
            visiting.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in by_key:
            visit(key)


class _FunctionTaskPlanner:
    def __init__(
        self,
        function: Callable[[TaskExecution], Any],
    ) -> None:
        self._function = function

    async def plan(self, execution: TaskExecution) -> TaskPlan:
        return await self._function(execution)


def normalize_task_planner(planner: TaskPlanner | Callable[..., Any]) -> TaskPlanner:
    normalized: Any = planner
    if callable(planner) and not callable(getattr(planner, "plan", None)):
        if not inspect.iscoroutinefunction(planner):
            raise TypeError("callable task planner must be async")
        normalized = _FunctionTaskPlanner(planner)

    plan = getattr(normalized, "plan", None)
    if not callable(plan):
        raise TypeError("task planner must define async plan(execution)")
    return normalized


__all__ = [
    "TaskPlan",
    "TaskPlanNode",
    "TaskPlanner",
    "normalize_task_planner",
]

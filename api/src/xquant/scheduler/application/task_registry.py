from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from xquant.scheduler.domain import TaskContext, TaskDefinition, TaskResult


@runtime_checkable
class TaskHandler(Protocol):
    """Contract implemented by every executable scheduler task."""

    async def execute(self, context: TaskContext) -> TaskResult:
        ...


class TaskRegistrationError(ValueError):
    """Base error for invalid task registrations."""


class TaskAlreadyRegistered(TaskRegistrationError):
    """Raised when a task name is registered more than once."""


class TaskNotFound(KeyError):
    """Raised when a requested task is not registered."""


class _FunctionTaskHandler:
    def __init__(
        self,
        function: Callable[[TaskContext], Awaitable[TaskResult]],
    ) -> None:
        self._function = function

    async def execute(self, context: TaskContext) -> TaskResult:
        return await self._function(context)


class TaskRegistry:
    """In-memory registry that binds task definitions to handlers."""

    def __init__(self) -> None:
        self._definitions: dict[str, TaskDefinition] = {}
        self._handlers: dict[str, TaskHandler] = {}

    def register(
        self,
        definition: TaskDefinition,
        handler: TaskHandler | type[TaskHandler] | Callable[..., Any],
        *,
        replace: bool = False,
    ) -> TaskHandler:
        name = definition.name
        if name in self._definitions and not replace:
            raise TaskAlreadyRegistered(f"task already registered: {name}")

        normalized = self._normalize_handler(handler)
        self._definitions[name] = definition
        self._handlers[name] = normalized
        return normalized

    def get_definition(self, name: str) -> TaskDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise TaskNotFound(name) from exc

    def get_handler(self, name: str) -> TaskHandler:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise TaskNotFound(name) from exc

    def list(self) -> list[TaskDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def items(self) -> list[tuple[TaskDefinition, TaskHandler]]:
        return [
            (self._definitions[name], self._handlers[name])
            for name in sorted(self._definitions)
        ]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    def task(
        self,
        *,
        name: str,
        handler: str | None = None,
        **definition_kwargs: Any,
    ) -> Callable[[Any], Any]:
        return task(
            name=name,
            handler=handler,
            registry=self,
            **definition_kwargs,
        )

    @staticmethod
    def _normalize_handler(
        handler: TaskHandler | type[TaskHandler] | Callable[..., Any],
    ) -> TaskHandler:
        normalized: Any = handler
        if inspect.isclass(handler):
            try:
                normalized = handler()
            except TypeError as exc:
                raise TypeError(
                    "task handler classes must be instantiable without arguments"
                ) from exc
        elif callable(handler) and not callable(getattr(handler, "execute", None)):
            normalized = _FunctionTaskHandler(handler)

        execute = getattr(normalized, "execute", None)
        if not callable(execute):
            raise TypeError("task handler must define execute(context)")
        if not inspect.iscoroutinefunction(execute):
            raise TypeError("task handler execute(context) must be async")
        return normalized


_DEFAULT_REGISTRY = TaskRegistry()


def default_registry() -> TaskRegistry:
    return _DEFAULT_REGISTRY


def task(
    *,
    name: str,
    handler: str | None = None,
    registry: TaskRegistry | None = None,
    **definition_kwargs: Any,
) -> Callable[[Any], Any]:
    """Register a class or async function as a scheduler task."""

    target_registry = registry or _DEFAULT_REGISTRY

    def decorator(handler_object: Any) -> Any:
        definition = TaskDefinition(
            name=name,
            handler=handler or _handler_name(handler_object),
            **definition_kwargs,
        )
        target_registry.register(definition, handler_object)
        setattr(  # noqa: B010 - decorators may receive classes and callables
            handler_object,
            "__scheduler_task_definition__",
            definition,
        )
        return handler_object

    return decorator


def _handler_name(handler: Any) -> str:
    if inspect.isclass(handler):
        return handler.__name__
    if inspect.isfunction(handler) or inspect.ismethod(handler):
        return handler.__qualname__
    return type(handler).__name__


__all__ = [
    "TaskAlreadyRegistered",
    "TaskHandler",
    "TaskNotFound",
    "TaskRegistrationError",
    "TaskRegistry",
    "default_registry",
    "task",
]

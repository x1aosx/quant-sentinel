from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CancellationManager(Protocol):
    """Store cancellation requests for scheduler executions."""

    async def request_cancel(
        self,
        execution_id: str,
        reason: str | None = None,
    ) -> bool:
        """Request cancellation and return whether this request was new."""
        ...

    async def is_cancelled(self, execution_id: str) -> bool:
        """Return whether an execution has a cancellation request."""
        ...

    async def get_reason(self, execution_id: str) -> str | None:
        """Return the cancellation reason when one can be read safely."""
        ...

    async def clear(self, execution_id: str) -> None:
        """Clear any cancellation request for an execution."""
        ...


__all__ = ["CancellationManager"]

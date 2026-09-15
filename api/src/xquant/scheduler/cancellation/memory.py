from __future__ import annotations

import asyncio

from .base import CancellationManager


class MemoryCancellationManager(CancellationManager):
    """Process-local cancellation state safe for concurrent async callers."""

    def __init__(self) -> None:
        self._reasons: dict[str, str | None] = {}
        self._guard = asyncio.Lock()

    async def request_cancel(
        self,
        execution_id: str,
        reason: str | None = None,
    ) -> bool:
        _validate_execution_id(execution_id)
        async with self._guard:
            if execution_id in self._reasons:
                return False
            self._reasons[execution_id] = reason
            return True

    async def is_cancelled(self, execution_id: str) -> bool:
        _validate_execution_id(execution_id)
        async with self._guard:
            return execution_id in self._reasons

    async def get_reason(self, execution_id: str) -> str | None:
        _validate_execution_id(execution_id)
        async with self._guard:
            return self._reasons.get(execution_id)

    async def clear(self, execution_id: str) -> None:
        _validate_execution_id(execution_id)
        async with self._guard:
            self._reasons.pop(execution_id, None)


def _validate_execution_id(execution_id: str) -> None:
    if not execution_id:
        raise ValueError("execution_id cannot be empty")


__all__ = ["MemoryCancellationManager"]

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    AccountSnapshot,
    ExecutionResult,
    OrderIntent,
    OrderPreview,
    Position,
)


@runtime_checkable
class ExecutionPort(Protocol):
    def account(self) -> AccountSnapshot: ...

    def positions(self) -> list[Position]: ...

    def preview(self, order: OrderIntent) -> OrderPreview: ...

    def place(self, order: OrderIntent) -> ExecutionResult: ...

    def cancel(self, order_id: str) -> None: ...

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from ..config import AlphaLabSettings
from ..domain import StrategyArtifact
from .errors import ExecutionDisabledError
from .models import (
    AccountSnapshot,
    ExecutionAuditRecord,
    ExecutionResult,
    ExecutionStatus,
    OrderIntent,
    OrderPreview,
    Position,
)
from .risk import ExecutionContext, RiskGate


class DisabledExecutionAdapter:
    """Safe default adapter that can never place a real order."""

    def __init__(self, reason: str = "AlphaLab live execution is disabled") -> None:
        self.reason = reason

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(account_id="disabled", equity=0.0, trading_enabled=False)

    def positions(self) -> list[Position]:
        return []

    def preview(self, order: OrderIntent) -> OrderPreview:
        return OrderPreview(
            order_id=order.order_id,
            estimated_notional=order.notional,
            accepted=False,
            message=self.reason,
        )

    def place(self, order: OrderIntent) -> ExecutionResult:
        raise ExecutionDisabledError(self.reason)

    def cancel(self, order_id: str) -> None:
        return None


class DryRunExecutionAdapter:
    """Simulates the execution port without contacting a broker."""

    def __init__(
        self,
        *,
        account_snapshot: AccountSnapshot | None = None,
        initial_positions: Sequence[Position] = (),
    ) -> None:
        self._account = account_snapshot or AccountSnapshot(
            account_id="dry-run",
            equity=1_000_000.0,
            cash=1_000_000.0,
        )
        self._positions = list(initial_positions)
        self.orders: list[OrderIntent] = []
        self.cancelled: list[str] = []

    def account(self) -> AccountSnapshot:
        return self._account

    def positions(self) -> list[Position]:
        return list(self._positions)

    def preview(self, order: OrderIntent) -> OrderPreview:
        return OrderPreview(
            order_id=order.order_id,
            estimated_notional=order.notional,
            estimated_fees=order.notional * 0.0003,
            estimated_slippage=order.notional * 0.0002,
            accepted=True,
            message="dry-run preview",
        )

    def place(self, order: OrderIntent) -> ExecutionResult:
        self.orders.append(order)
        return ExecutionResult(
            order_id=order.order_id,
            status=ExecutionStatus.DRY_RUN,
            accepted=True,
            external_order_id=f"dry-run-{uuid4().hex}",
            message="order simulated; no broker order was placed",
        )

    def cancel(self, order_id: str) -> None:
        self.cancelled.append(order_id)


@dataclass
class ExecutionService:
    """Apply risk checks before forwarding an intent to an execution port."""

    settings: AlphaLabSettings
    risk_gate: RiskGate
    adapter: DisabledExecutionAdapter | DryRunExecutionAdapter
    audit_log: list[ExecutionAuditRecord] = field(default_factory=list)

    def submit(
        self,
        order: OrderIntent,
        artifact: StrategyArtifact,
        *,
        account: AccountSnapshot | None = None,
        positions: Sequence[Position] | None = None,
        context: ExecutionContext | None = None,
    ) -> ExecutionResult:
        account = account or self.adapter.account()
        positions = list(self.adapter.positions() if positions is None else positions)
        decision = self.risk_gate.evaluate(
            order,
            artifact,
            account=account,
            positions=positions,
            context=context,
        )
        if not decision.allowed:
            result = ExecutionResult(
                order_id=order.order_id,
                status=ExecutionStatus.REJECTED,
                accepted=False,
                message=decision.message,
                raw={"risk_code": decision.code},
            )
            self.audit_log.append(
                ExecutionAuditRecord(order=order, decision=decision, result=result)
            )
            return result

        result = self.adapter.place(order)
        self.audit_log.append(
            ExecutionAuditRecord(order=order, decision=decision, result=result)
        )
        return result

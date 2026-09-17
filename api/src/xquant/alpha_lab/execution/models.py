from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ExecutionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DRY_RUN = "DRY_RUN"
    DISABLED = "DISABLED"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    equity: float
    cash: float = 0.0
    gross_exposure: float = 0.0
    daily_pnl: float = 0.0
    currency: str = "CNY"
    trading_enabled: bool = True


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    price: float = 0.0
    market_value: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    reference_price: float | None = None
    signal_key: str = ""
    bar_close_ts: str = ""
    created_at: str = field(default_factory=_utc_now)
    reduce_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def signed_quantity(self) -> float:
        multiplier = 1.0 if self.side is OrderSide.BUY else -1.0
        return multiplier * abs(float(self.quantity))

    @property
    def notional(self) -> float:
        price = (
            self.limit_price
            if self.limit_price is not None
            else self.reference_price
            if self.reference_price is not None
            else 0.0
        )
        return abs(float(self.quantity)) * float(price)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["side"] = self.side.value
        payload["order_type"] = self.order_type.value
        return payload


@dataclass(frozen=True)
class OrderPreview:
    order_id: str
    estimated_notional: float
    estimated_fees: float = 0.0
    estimated_slippage: float = 0.0
    accepted: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionResult:
    order_id: str
    status: ExecutionStatus
    accepted: bool
    external_order_id: str = ""
    message: str = ""
    created_at: str = field(default_factory=_utc_now)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    code: str = "allowed"
    message: str = ""
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionAuditRecord:
    order: OrderIntent
    decision: RiskDecision
    result: ExecutionResult | None
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order.to_dict(),
            "decision": self.decision.to_dict(),
            "result": self.result.to_dict() if self.result is not None else None,
            "created_at": self.created_at,
        }

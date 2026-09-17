"""Guarded execution boundary for AlphaLab strategy signals."""

from .adapters import (
    DisabledExecutionAdapter,
    DryRunExecutionAdapter,
    ExecutionService,
)
from .errors import (
    ExecutionDisabledError,
    ExecutionRejectedError,
    RiskRejectedError,
)
from .kill_switch import KillSwitch
from .models import (
    AccountSnapshot,
    ExecutionAuditRecord,
    ExecutionResult,
    ExecutionStatus,
    OrderIntent,
    OrderPreview,
    OrderSide,
    OrderType,
    Position,
    RiskDecision,
)
from .ports import ExecutionPort
from .risk import ExecutionContext, OrderRiskContext, RiskGate, RiskLimits

__all__ = [
    "AccountSnapshot",
    "DisabledExecutionAdapter",
    "DryRunExecutionAdapter",
    "ExecutionAuditRecord",
    "ExecutionContext",
    "ExecutionDisabledError",
    "ExecutionPort",
    "ExecutionRejectedError",
    "ExecutionResult",
    "ExecutionService",
    "ExecutionStatus",
    "KillSwitch",
    "OrderIntent",
    "OrderPreview",
    "OrderRiskContext",
    "OrderSide",
    "OrderType",
    "Position",
    "RiskDecision",
    "RiskGate",
    "RiskLimits",
    "RiskRejectedError",
]

"""Production backtest, cost, metrics, and robustness primitives for AlphaLab."""

from .costs import (
    AShareCostConfig,
    AShareCostModel,
    CostBreakdown,
    CostConfig,
    CostModel,
)
from .engine import BacktestConfig, BacktestEngine
from .execution_model import (
    AShareExecutionConfig,
    AShareExecutionModel,
    ExecutionConfig,
    ExecutionDecision,
    ExecutionModel,
    T1ExecutionModel,
)
from .metrics import calculate_metrics, infer_periods_per_year
from .report import (
    BacktestReport,
    ExecutionRecord,
    SymbolBacktestResult,
    TradeRecord,
)
from .robustness import RobustnessAuditor, RobustnessAuditResult

__all__ = [
    "AShareCostConfig",
    "AShareCostModel",
    "AShareExecutionConfig",
    "AShareExecutionModel",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestReport",
    "CostBreakdown",
    "CostConfig",
    "CostModel",
    "ExecutionConfig",
    "ExecutionDecision",
    "ExecutionModel",
    "ExecutionRecord",
    "RobustnessAuditResult",
    "RobustnessAuditor",
    "SymbolBacktestResult",
    "T1ExecutionModel",
    "TradeRecord",
    "calculate_metrics",
    "infer_periods_per_year",
]

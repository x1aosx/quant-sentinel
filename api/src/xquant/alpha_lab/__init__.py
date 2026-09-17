"""Alpha factor research and strategy production subsystem."""

from .config import AlphaLabSettings
from .data import BarFrame, DataSnapshot, MarketDataPort, XQSMarketDataAdapter
from .domain import (
    BacktestResult,
    Direction,
    SignalDecision,
    StrategyArtifact,
    StrategyStatus,
    TrainingRun,
    TrainingStatus,
)
from .factor import (
    FEATURE_REGISTRY,
    FORMULA_VOCAB,
    OPERATOR_REGISTRY,
    FactorRuntime,
    FeatureRegistry,
    OperatorRegistry,
    SignalKernel,
)

__all__ = [
    "FEATURE_REGISTRY",
    "FORMULA_VOCAB",
    "OPERATOR_REGISTRY",
    "AlphaLabSettings",
    "BacktestResult",
    "BarFrame",
    "DataSnapshot",
    "Direction",
    "FactorRuntime",
    "FeatureRegistry",
    "MarketDataPort",
    "OperatorRegistry",
    "SignalDecision",
    "SignalKernel",
    "StrategyArtifact",
    "StrategyStatus",
    "TrainingRun",
    "TrainingStatus",
    "XQSMarketDataAdapter",
]

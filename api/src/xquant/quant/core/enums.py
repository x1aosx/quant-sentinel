"""Stable string enums shared by the quantitative analysis layers."""

from __future__ import annotations

from enum import Enum


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Trend(_ValueEnum):
    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"
    UNKNOWN = "UNKNOWN"


class MarketPhase(_ValueEnum):
    TRENDING = "TRENDING"
    PULLBACK = "PULLBACK"
    CONSOLIDATION = "CONSOLIDATION"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    EXHAUSTION = "EXHAUSTION"
    UNKNOWN = "UNKNOWN"


class ExecutionState(_ValueEnum):
    WAIT = "WAIT"
    WATCH = "WATCH"
    READY = "READY"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"


class AlignmentState(_ValueEnum):
    FULL_ALIGNMENT = "FULL_ALIGNMENT"
    PARTIAL_ALIGNMENT = "PARTIAL_ALIGNMENT"
    MIXED = "MIXED"
    HIGH_CONFLICT = "HIGH_CONFLICT"


class VolumeState(_ValueEnum):
    EXPANDING = "EXPANDING"
    NORMAL = "NORMAL"
    SHRINKING = "SHRINKING"
    UNKNOWN = "UNKNOWN"


class PriceStructure(_ValueEnum):
    HIGHER_HIGH_HIGHER_LOW = "HIGHER_HIGH_HIGHER_LOW"
    LOWER_LOW_LOWER_HIGH = "LOWER_LOW_LOWER_HIGH"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class DecisionAction(_ValueEnum):
    AVOID = "AVOID"
    WATCH = "WATCH"
    WAIT_BUY = "WAIT_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    WAIT_SELL = "WAIT_SELL"
    SELL = "SELL"


TrendDirection = Trend
Phase = MarketPhase
Action = DecisionAction

__all__ = [
    "Action",
    "AlignmentState",
    "DecisionAction",
    "ExecutionState",
    "MarketPhase",
    "Phase",
    "PriceStructure",
    "Trend",
    "TrendDirection",
    "VolumeState",
]

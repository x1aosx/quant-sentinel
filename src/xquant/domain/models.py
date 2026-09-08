from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence


class IntentAction(StrEnum):
    WATCH = "WATCH"
    PROPOSE_ENTRY = "PROPOSE_ENTRY"
    PROPOSE_EXIT = "PROPOSE_EXIT"
    CANCEL_PLAN = "CANCEL_PLAN"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class Bar:
    instrument_id: str
    session_id: str
    open: float
    high: float
    low: float
    close: float
    volume_shares: float
    turnover_currency: float
    source: str
    received_at: datetime
    available_at: datetime
    revision_id: str

    def validate(self) -> None:
        if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
            raise ValueError("Prices must be positive")
        if self.low > min(self.open, self.close):
            raise ValueError("low > min(open, close)")
        if self.high < max(self.open, self.close):
            raise ValueError("high < max(open, close)")
        if self.volume_shares < 0 or self.turnover_currency < 0:
            raise ValueError("Volume and turnover cannot be negative")


@dataclass(frozen=True)
class EvaluationContext:
    event_kind: str
    decision_at: datetime
    snapshot_id: str
    calendar_version: str
    execution_profile: str
    state: Mapping[str, Any]
    position_view: Mapping[str, Any]
    features: Mapping[str, Any]


@dataclass(frozen=True)
class StrategyIntent:
    instrument_id: str
    action: IntentAction
    reason_codes: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationResult:
    next_state: Mapping[str, Any]
    intents: Sequence[StrategyIntent]
    trace: Sequence[Mapping[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TradePlan:
    plan_id: str
    strategy_id: str
    strategy_version: str
    instrument_id: str
    decision_at: datetime
    execution_session_id: str
    execution_profile: str
    entry_reference: float
    entry_min: float
    entry_max: float
    stop_threshold: float
    target_threshold: float
    quantity_cap: int
    reason_codes: tuple[str, ...]
    simulation_only: bool
    status: str = "PLANNED"
    data_snapshot_id: str = ""
    level_id: str = ""
    config_hash: str = ""
    run_id: str = ""


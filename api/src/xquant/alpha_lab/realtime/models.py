from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..domain import Direction, SignalState


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RealtimeWatch:
    id: str
    source: str
    symbol: str
    timeframe: str
    strategy_id: str
    strategy_version: str
    state: SignalState = SignalState.PENDING
    last_closed_bar_ts: str = ""
    last_factor: float | None = None
    last_position: float | None = None
    last_direction: Direction = Direction.FLAT
    last_strength: float | None = None
    updated_at: str = field(default_factory=_utc_now)
    error: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RealtimeWatch:
        data = dict(payload)
        data["state"] = SignalState(str(data.get("state") or SignalState.PENDING.value))
        data["last_direction"] = Direction(
            str(data.get("last_direction") or Direction.FLAT.value)
        )
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["last_direction"] = self.last_direction.value
        return payload


@dataclass(frozen=True)
class SignalRecord:
    signal_key: str
    bar_key: str
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    source: str
    bar_close_ts: str
    direction: Direction
    position: float
    strength: float
    factor_value: float
    state: SignalState
    bars_used: int
    generated_at: str = field(default_factory=_utc_now)
    message: str = ""
    idempotent: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SignalRecord:
        data = dict(payload)
        data["direction"] = Direction(str(data["direction"]))
        data["state"] = SignalState(str(data["state"]))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True)
class DirectionFlipEvent:
    symbol: str
    timeframe: str
    strategy_id: str
    strategy_version: str
    previous_direction: Direction
    direction: Direction
    position: float
    strength: float
    factor_value: float
    bar_close_ts: str
    signal_key: str
    event_type: str = "alpha.signal.changed"
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["previous_direction"] = self.previous_direction.value
        payload["direction"] = self.direction.value
        return payload

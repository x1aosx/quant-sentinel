from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSize:
    quantity: int
    budgeted_risk: float
    reason_codes: tuple[str, ...]


def size_position(
    *,
    equity: float,
    entry: float,
    stop: float,
    risk_fraction: float = 0.0025,
    gap_buffer_atr: float = 0.5,
    atr: float = 0.0,
    per_share_cost: float = 0.0,
    lot_size: int = 100,
    max_quantity: int | None = None,
) -> PositionSize:
    if equity <= 0 or entry <= 0 or stop >= entry:
        return PositionSize(0, 0.0, ("INVALID_INPUT",))
    risk_per_share = max(entry - stop, 0.01) + gap_buffer_atr * atr + per_share_cost
    q = int(equity * risk_fraction / risk_per_share)
    q = (q // lot_size) * lot_size
    if max_quantity is not None:
        q = min(q, max_quantity)
    return PositionSize(q, q * risk_per_share, ("RISK_BUDGET_OK",) if q > 0 else ("BELOW_LOT_SIZE",))


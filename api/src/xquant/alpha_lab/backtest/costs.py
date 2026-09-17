from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AShareCostConfig:
    """Cost rates expressed as fractions of traded notional."""

    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.0002

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

    def to_dict(self) -> dict[str, float]:
        return {
            "commission_rate": self.commission_rate,
            "stamp_duty_rate": self.stamp_duty_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_rate": self.slippage_rate,
        }


@dataclass(frozen=True)
class CostBreakdown:
    turnover: float
    turnover_notional: float
    buy_turnover: float
    sell_turnover: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    total: float

    @property
    def total_cost(self) -> float:
        return self.total

    @property
    def notional_turnover(self) -> float:
        return self.turnover_notional

    def to_dict(self) -> dict[str, float]:
        return {
            "turnover": self.turnover,
            "turnover_notional": self.turnover_notional,
            "buy_turnover": self.buy_turnover,
            "sell_turnover": self.sell_turnover,
            "commission": self.commission,
            "stamp_duty": self.stamp_duty,
            "transfer_fee": self.transfer_fee,
            "slippage": self.slippage,
            "total": self.total,
        }


class AShareCostModel:
    """Cost model with asymmetric stamp duty and actual notional turnover."""

    name = "a_share_cost"

    def __init__(
        self,
        config: AShareCostConfig | None = None,
        *,
        commission_rate: float | None = None,
        stamp_duty_rate: float | None = None,
        transfer_fee_rate: float | None = None,
        slippage_rate: float | None = None,
    ) -> None:
        base = config or AShareCostConfig()
        self.config = AShareCostConfig(
            commission_rate=(
                base.commission_rate
                if commission_rate is None
                else commission_rate
            ),
            stamp_duty_rate=(
                base.stamp_duty_rate
                if stamp_duty_rate is None
                else stamp_duty_rate
            ),
            transfer_fee_rate=(
                base.transfer_fee_rate
                if transfer_fee_rate is None
                else transfer_fee_rate
            ),
            slippage_rate=(
                base.slippage_rate if slippage_rate is None else slippage_rate
            ),
        )

    def calculate(
        self,
        delta: float | None = None,
        equity: float = 0.0,
        price: float = 1.0,
        *,
        side: str | None = None,
        turnover: float | None = None,
    ) -> CostBreakdown:
        """Calculate costs from an exposure change or an explicit turnover.

        ``delta`` is the executed target-weight change. A positive delta is a
        buy and a negative delta is a sell. When ``turnover`` is supplied
        without ``delta``, ``side`` determines stamp-duty treatment.
        """

        if not np.isfinite(equity) or equity < 0:
            raise ValueError("equity must be finite and non-negative")
        if not np.isfinite(price) or price <= 0:
            raise ValueError("price must be finite and positive")
        if delta is not None and not np.isfinite(delta):
            raise ValueError("delta must be finite")
        if turnover is not None and (not np.isfinite(turnover) or turnover < 0):
            raise ValueError("turnover must be finite and non-negative")

        if delta is None:
            exposure_turnover = 0.0 if turnover is None else abs(float(turnover))
            normalized_side = (side or "buy").strip().lower()
            if normalized_side not in {"buy", "sell"}:
                raise ValueError("side must be 'buy' or 'sell'")
            buy_turnover = exposure_turnover if normalized_side == "buy" else 0.0
            sell_turnover = exposure_turnover if normalized_side == "sell" else 0.0
        else:
            exposure_turnover = abs(float(delta))
            if exposure_turnover == 0.0:
                buy_turnover = 0.0
                sell_turnover = 0.0
            elif delta > 0:
                buy_turnover = exposure_turnover
                sell_turnover = 0.0
            else:
                buy_turnover = 0.0
                sell_turnover = exposure_turnover

        turnover_notional = exposure_turnover * equity
        sell_notional = sell_turnover * equity
        commission = (
            turnover_notional * self.config.commission_rate
        )
        stamp_duty = sell_notional * self.config.stamp_duty_rate
        transfer_fee = turnover_notional * self.config.transfer_fee_rate
        slippage = turnover_notional * self.config.slippage_rate
        total = commission + stamp_duty + transfer_fee + slippage
        return CostBreakdown(
            turnover=float(exposure_turnover),
            turnover_notional=float(turnover_notional),
            buy_turnover=float(buy_turnover),
            sell_turnover=float(sell_turnover),
            commission=float(commission),
            stamp_duty=float(stamp_duty),
            transfer_fee=float(transfer_fee),
            slippage=float(slippage),
            total=float(total),
        )

    def with_stress(self, multiplier: float) -> AShareCostModel:
        if not np.isfinite(multiplier) or multiplier < 0:
            raise ValueError("multiplier must be finite and non-negative")
        config = replace(
            self.config,
            commission_rate=self.config.commission_rate * multiplier,
            stamp_duty_rate=self.config.stamp_duty_rate * multiplier,
            transfer_fee_rate=self.config.transfer_fee_rate * multiplier,
            slippage_rate=self.config.slippage_rate * multiplier,
        )
        return AShareCostModel(config)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.config.to_dict()}


CostConfig = AShareCostConfig
CostModel = AShareCostModel

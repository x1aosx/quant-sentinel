from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real

from ..config import AlphaLabSettings
from ..domain import StrategyArtifact, StrategyStatus
from .errors import RiskRejectedError
from .kill_switch import KillSwitch
from .models import AccountSnapshot, OrderIntent, Position, RiskDecision


@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: float | None = None
    max_gross_exposure: float | None = None
    max_price_deviation: float | None = 0.05
    max_signal_age_seconds: float | None = None
    daily_loss_limit: float | None = None
    require_production: bool = True
    require_market_open: bool = True
    allowed_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionContext:
    execution_enabled: bool | None = None
    user_permitted: bool = True
    market_open: bool = True
    signal_age_seconds: float = 0.0
    duplicate_order: bool = False
    reference_price: float | None = None


OrderRiskContext = ExecutionContext


class RiskGate:
    """Central gate between a realtime signal and any execution adapter."""

    def __init__(
        self,
        settings: AlphaLabSettings | None = None,
        *,
        limits: RiskLimits | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.settings = settings or AlphaLabSettings()
        self.limits = limits or RiskLimits()
        self.kill_switch = kill_switch or KillSwitch()

    def evaluate(
        self,
        order: OrderIntent,
        artifact: StrategyArtifact,
        *,
        account: AccountSnapshot | None = None,
        positions: Sequence[Position] = (),
        context: ExecutionContext | None = None,
    ) -> RiskDecision:
        context = context or ExecutionContext()
        execution_enabled = (
            self.settings.execution_enabled
            if context.execution_enabled is None
            else context.execution_enabled
        )
        checks: dict[str, bool] = {}

        checks["feature_flag"] = bool(execution_enabled)
        if not checks["feature_flag"]:
            return self._reject(
                checks,
                "execution_disabled",
                "AlphaLab execution is disabled by default",
            )

        checks["kill_switch"] = not self.kill_switch.is_active()
        if not checks["kill_switch"]:
            return self._reject(
                checks,
                "kill_switch_active",
                self.kill_switch.reason or "execution kill switch is active",
            )

        checks["user_permission"] = bool(context.user_permitted)
        if not checks["user_permission"]:
            return self._reject(checks, "permission_denied", "user lacks execution permission")

        checks["strategy_production"] = (
            not self.limits.require_production
            or artifact.status is StrategyStatus.PRODUCTION
        )
        if not checks["strategy_production"]:
            return self._reject(
                checks,
                "strategy_not_production",
                "only PRODUCTION strategies may be executed",
            )

        checks["market_open"] = (
            not self.limits.require_market_open or bool(context.market_open)
        )
        if not checks["market_open"]:
            return self._reject(checks, "market_closed", "market is closed")

        checks["duplicate_order"] = not context.duplicate_order
        if not checks["duplicate_order"]:
            return self._reject(checks, "duplicate_order", "order was already submitted")

        checks["fresh_signal"] = self._signal_is_fresh(context)
        if not checks["fresh_signal"]:
            return self._reject(checks, "stale_signal", "signal exceeds maximum age")

        checks["symbol_allowed"] = (
            not self.limits.allowed_symbols
            or order.symbol.upper() in {item.upper() for item in self.limits.allowed_symbols}
        )
        if not checks["symbol_allowed"]:
            return self._reject(checks, "symbol_not_allowed", "symbol is not in the allowlist")

        checks["order_size"] = self._order_size_allowed(order)
        if not checks["order_size"]:
            return self._reject(
                checks,
                "max_order_notional",
                "order notional exceeds configured limit",
            )

        checks["price_deviation"] = self._price_deviation_allowed(order, context)
        if not checks["price_deviation"]:
            return self._reject(
                checks,
                "price_deviation",
                "order price exceeds reference-price tolerance",
            )

        checks["gross_exposure"] = self._gross_exposure_allowed(
            order,
            account=account,
            positions=positions,
        )
        if not checks["gross_exposure"]:
            return self._reject(
                checks,
                "max_gross_exposure",
                "projected gross exposure exceeds configured limit",
            )

        checks["daily_loss"] = self._daily_loss_allowed(account)
        if not checks["daily_loss"]:
            return self._reject(
                checks,
                "daily_loss_limit",
                "daily loss limit has been reached",
            )
        return RiskDecision(allowed=True, checks=checks)

    check = evaluate

    def ensure_allowed(
        self,
        order: OrderIntent,
        artifact: StrategyArtifact,
        **kwargs: object,
    ) -> RiskDecision:
        decision = self.evaluate(order, artifact, **kwargs)
        if not decision.allowed:
            raise RiskRejectedError(decision.message or decision.code)
        return decision

    @staticmethod
    def _reject(
        checks: dict[str, bool],
        code: str,
        message: str,
    ) -> RiskDecision:
        return RiskDecision(allowed=False, code=code, message=message, checks=dict(checks))

    def _signal_is_fresh(self, context: ExecutionContext) -> bool:
        maximum = self.limits.max_signal_age_seconds
        if maximum is None:
            return True
        age = float(context.signal_age_seconds)
        return age >= 0 and age <= maximum

    def _order_size_allowed(self, order: OrderIntent) -> bool:
        maximum = self.limits.max_order_notional
        if maximum is None:
            return True
        if order.notional <= 0:
            return False
        return order.notional <= maximum

    def _price_deviation_allowed(
        self,
        order: OrderIntent,
        context: ExecutionContext,
    ) -> bool:
        maximum = self.limits.max_price_deviation
        if maximum is None:
            return True
        reference = context.reference_price or order.reference_price
        candidate = order.limit_price
        if reference is None or candidate is None:
            return True
        if not isinstance(reference, Real) or float(reference) <= 0:
            return False
        return abs(float(candidate) - float(reference)) / float(reference) <= maximum

    def _gross_exposure_allowed(
        self,
        order: OrderIntent,
        *,
        account: AccountSnapshot | None,
        positions: Sequence[Position],
    ) -> bool:
        maximum = self.limits.max_gross_exposure
        if maximum is None:
            return True
        current = (
            float(account.gross_exposure)
            if account is not None
            else sum(abs(float(position.market_value)) for position in positions)
        )
        return current + order.notional <= maximum

    def _daily_loss_allowed(self, account: AccountSnapshot | None) -> bool:
        maximum = self.limits.daily_loss_limit
        if maximum is None or account is None:
            return True
        return float(account.daily_pnl) >= -abs(float(maximum))

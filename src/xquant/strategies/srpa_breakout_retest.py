from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from xquant.domain.models import EvaluationContext, EvaluationResult, IntentAction, StrategyIntent
from xquant.regimes.classifier import classify_regime


@dataclass
class FrozenSetup:
    breakout_session: str
    zone_high: float
    zone_low: float
    atr: float
    state: str
    retest_seen: bool = False
    confirmation_session: str | None = None
    entry_reference: float | None = None
    stop: float | None = None
    target: float | None = None
    quantity_cap: int = 0


class SRPABreakoutRetest:
    strategy_id = "xq.srpa.breakout_retest.long"
    strategy_version = "0.1.0"

    def evaluate(self, ctx: EvaluationContext) -> EvaluationResult:
        state = dict(ctx.state)
        setup = state.get("setup")
        if setup is None:
            setup = FrozenSetup("", 0.0, 0.0, 0.0, "IDLE")
            state["setup"] = setup
        trace: list[Mapping[str, Any]] = []
        intents: list[StrategyIntent] = []
        f = ctx.features
        if not f or "close" not in f:
            return EvaluationResult(state, intents, trace)
        close = float(f["close"])
        high = float(f["high"])
        low = float(f["low"])
        atr = float(f.get("atr14") or 0.0)
        regime = classify_regime(f).regime
        trace.append({"session": f.get("session_id"), "state": setup.state, "regime": regime})

        if setup.state == "IDLE":
            levels = f.get("levels") or []
            resistances = [lv for lv in levels if lv.kind == "resistance" and lv.confirmed_pivot_count >= 2]
            if resistances:
                zone = min(resistances, key=lambda lv: abs(lv.center - close))
                if atr > 0 and close > zone.high + 0.2 * atr and close > float(f["open"]) and float(f.get("clv", 0)) >= 0.65 and float(f.get("body_ratio", 0)) >= 0.5:
                    setup = FrozenSetup(str(f.get("session_id")), zone.high, zone.low, atr, "BREAKOUT_SEEN")
                    state["setup"] = setup
                    trace.append({"event": "BREAKOUT_SEEN"})
        elif setup.state == "BREAKOUT_SEEN":
            if close > setup.zone_high:
                setup.state = "HELD_ABOVE"
                state["setup"] = setup
                trace.append({"event": "HELD_ABOVE"})
        elif setup.state == "HELD_ABOVE":
            if low <= setup.zone_high + 0.35 * setup.atr and close >= setup.zone_low:
                setup.retest_seen = True
                setup.state = "RETEST_SEEN"
                state["setup"] = setup
                trace.append({"event": "RETEST_SEEN"})
        elif setup.state == "RETEST_SEEN":
            prev_high = float(f.get("prev_high") or close)
            if close > float(f["open"]) and float(f.get("clv", 0)) >= 0.65 and close > setup.zone_high + 0.1 * setup.atr and close > prev_high:
                setup.confirmation_session = str(f.get("session_id"))
                setup.entry_reference = close
                setup.stop = min(setup.zone_low, low) - 0.2 * setup.atr
                setup.target = setup.entry_reference + 2.0 * (setup.entry_reference - setup.stop)
                setup.state = "CONFIRMED"
                state["setup"] = setup
                intents.append(
                    StrategyIntent(
                        instrument_id=ctx.features.get("instrument_id", ""),
                        action=IntentAction.PROPOSE_ENTRY,
                        reason_codes=("BREAKOUT_RETEST_CONFIRMED", "RISK_BUDGET_OK"),
                        payload={
                            "entry_reference": setup.entry_reference,
                            "stop": setup.stop,
                            "target": setup.target,
                            "quantity_cap": setup.quantity_cap,
                        },
                    )
                )
                trace.append({"event": "CONFIRMED", "plan": True})
        if setup.state not in ("CONFIRMED",) and setup.breakout_session and f.get("close") is not None:
            if close < setup.zone_low - 0.5 * setup.atr or regime in ("INVALID", "CHAOS", "DOWNTREND"):
                setup.state = "INVALIDATED"
                state["setup"] = setup
                trace.append({"event": "INVALIDATED"})
        return EvaluationResult(state, intents, trace)


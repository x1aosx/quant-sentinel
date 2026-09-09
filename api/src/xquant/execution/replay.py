from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from xquant.domain.models import Bar, EvaluationContext, IntentAction
from xquant.features.indicators import compute_features
from xquant.levels.sr_v01 import detect_levels
from xquant.regimes.classifier import classify_regime, market_gate
from xquant.strategies.srpa_breakout_retest import SRPABreakoutRetest


@dataclass
class ReplayResult:
    run_id: str
    instrument_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _asdict_plan(plan, run_id: str) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "strategy_id": plan.strategy_id,
        "strategy_version": plan.strategy_version,
        "instrument_id": plan.instrument_id,
        "decision_at": plan.decision_at.isoformat(),
        "execution_session_id": plan.execution_session_id,
        "entry_reference": plan.entry_reference,
        "entry_min": plan.entry_min,
        "entry_max": plan.entry_max,
        "stop_threshold": plan.stop_threshold,
        "target_threshold": plan.target_threshold,
        "quantity_cap": plan.quantity_cap,
        "status": plan.status,
        "simulation_only": plan.simulation_only,
        "run_id": run_id,
    }


def run_replay(
    bars: list[Bar],
    *,
    run_id: str = "demo-run",
    initial_equity: float = 100_000.0,
    benchmark_bars: list[Bar] | None = None,
) -> ReplayResult:
    strategy = SRPABreakoutRetest()
    state: dict[str, Any] = {}
    result = ReplayResult(run_id=run_id, instrument_id=bars[0].instrument_id if bars else "")
    equity = initial_equity
    features_all = compute_features(bars)
    for i in range(len(bars)):
        visible = bars[: i + 1]
        if len(visible) < 30:
            continue
        fdf = compute_features(visible)
        row = fdf.iloc[-1]
        feature_map = row.to_dict()
        feature_map["instrument_id"] = bars[i].instrument_id
        feature_map["session_id"] = bars[i].session_id
        feature_map["data_valid"] = True
        feature_map["levels"] = detect_levels(fdf)
        if i >= 1:
            feature_map["prev_high"] = float(fdf.iloc[-2]["high"])
        gate = "RISK_ON"
        if benchmark_bars and i >= 20:
            bdf = compute_features(benchmark_bars[: i + 1])
            gate = market_gate(bdf.iloc[-1].to_dict())
        ctx = EvaluationContext(
            event_kind="BarsPublished",
            decision_at=bars[i].available_at,
            snapshot_id=f"snap-{run_id}-{i}",
            calendar_version="demo",
            execution_profile="EOD_ASSIST",
            state=state,
            position_view={"cash": equity, "positions": []},
            features=feature_map,
        )
        ev = strategy.evaluate(ctx)
        state = dict(ev.next_state)
        result.events.extend(
            {
                "session": bars[i].session_id,
                "available_at": bars[i].available_at.isoformat(),
                **{k: v for k, v in tr.items()},
            }
            for tr in ev.trace
        )
        for intent in ev.intents:
            if intent.action == IntentAction.PROPOSE_ENTRY:
                payload = intent.payload
                plan = _make_plan(bars, i, payload, run_id)
                result.plans.append(_asdict_plan(plan, run_id))
        equity = equity * 1.0005
        result.equity_curve.append(
            {
                "session": bars[i].session_id,
                "available_at": bars[i].available_at.isoformat(),
                "equity": round(equity, 2),
                "regime": classify_regime(feature_map).regime,
            }
        )
    result.summary = {
        "run_id": run_id,
        "instrument_id": result.instrument_id,
        "bars": len(result.events),
        "plans": len(result.plans),
        "simulation_only": True,
        "demo": True,
    }
    return result


def _make_plan(bars: list[Bar], i: int, payload: Mapping[str, Any], run_id: str):
    from xquant.domain.models import TradePlan

    next_session = bars[i + 1].session_id if i + 1 < len(bars) else "NONE"
    entry_ref = float(payload.get("entry_reference") or 0.0)
    stop = float(payload.get("stop") or 0.0)
    target = float(payload.get("target") or 0.0)
    atr = float(payload.get("atr") or 0.0)
    entry_min = max(stop + 0.01, entry_ref - 0.25 * atr)
    entry_max = entry_ref + 0.5 * atr
    return TradePlan(
        plan_id=f"{run_id}:{bars[i].session_id}",
        strategy_id=SRPABreakoutRetest.strategy_id,
        strategy_version=SRPABreakoutRetest.strategy_version,
        instrument_id=bars[i].instrument_id,
        decision_at=bars[i].available_at,
        execution_session_id=next_session,
        execution_profile="EOD_ASSIST",
        entry_reference=entry_ref,
        entry_min=entry_min,
        entry_max=entry_max,
        stop_threshold=stop,
        target_threshold=target,
        quantity_cap=int(payload.get("quantity_cap") or 0),
        reason_codes=("BREAKOUT_RETEST_CONFIRMED",),
        simulation_only=True,
        data_snapshot_id="demo-snapshot",
        level_id="demo-frozen-level",
        config_hash="DEMO-NOT-A-REAL-HASH",
        run_id=run_id,
    )


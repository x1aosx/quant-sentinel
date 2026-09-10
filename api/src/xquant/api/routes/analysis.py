from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from xquant.analysis.price_action import analyze_price_action
from xquant.analysis.sr_levels import detect_support_resistance
from xquant.registry import Database

from ..dependencies import get_database, get_dataset_or_404
from ..validation import int_in, number_in

router = APIRouter(tags=["analysis"])


@router.post("/analysis/support-resistance")
def support_resistance(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        result = detect_support_resistance(
            dataset["bars"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            lookback=int_in(payload.get("lookback"), 60, 1000, 250),
            n_zones=int_in(payload.get("n_zones"), 2, 8, 6),
            direction=str(payload.get("direction") or "both"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    risk_reward = result.setdefault("summary", {}).setdefault("risk_reward", {})
    risk_reward.setdefault("risk_reward_ratio", risk_reward.get("ratio"))
    price = float(result.get("current_price") or 0.0)
    atr = float(result.get("atr") or 0.0)
    if price and atr:
        risk_reward.setdefault(
            "potential_profit_pct",
            float(risk_reward.get("potential_reward_atr") or 0.0) * atr / price * 100.0,
        )
        risk_reward.setdefault(
            "potential_loss_pct",
            float(risk_reward.get("potential_risk_atr") or 0.0) * atr / price * 100.0,
        )
    result["candles"] = dataset["bars"][-150:]
    return result


@router.post("/analysis/price-action")
def price_action(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        result = analyze_price_action(
            dataset["bars"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            lookback=int_in(payload.get("lookback"), 60, 500, 120),
            risk_fraction=number_in(payload.get("risk_fraction"), 0.0005, 0.1, 0.01),
            min_rr=number_in(payload.get("min_rr"), 0.5, 10.0, 1.5),
            stance=str(payload.get("stance") or "conservative"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    atr = float(result.get("atr") or 0.0)
    supports = [float(value) for value in result.get("features", {}).get("supports", [])]
    resistances = [float(value) for value in result.get("features", {}).get("resistances", [])]
    levels = []
    for price in supports:
        half = max(atr * 0.25, price * 0.001)
        levels.append({"zone_type": "support", "center": price, "low": price - half, "high": price + half})
    for price in resistances:
        half = max(atr * 0.25, price * 0.001)
        levels.append(
            {"zone_type": "resistance", "center": price, "low": price - half, "high": price + half}
        )
    result["levels"] = levels
    result["candles"] = dataset["bars"][-150:]
    return result

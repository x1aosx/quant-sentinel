from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from xquant.analysis.price_action import analyze_price_action
from xquant.analysis.sr_levels import detect_support_resistance
from xquant.registry import Database

from ..dependencies import get_database, get_dataset_or_404
from ..validation import int_in, number_in

router = APIRouter(tags=["analysis"])


def _change_pct(bars: list[dict[str, Any]]) -> float | None:
    if len(bars) < 2:
        return None
    previous = float(bars[-2].get("close") or 0.0)
    current = float(bars[-1].get("close") or 0.0)
    if previous == 0.0:
        return None
    return round((current - previous) / previous * 100.0, 4)


def _summary_item(dataset: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    summary = dataset["summary"]
    result = detect_support_resistance(
        bars,
        symbol=str(summary["symbol"]),
        timeframe=str(summary["timeframe"]),
        lookback=250,
        n_zones=6,
        direction="both",
    )
    levels = list(result.get("levels") or [])
    supports = [level for level in levels if level.get("zone_type") == "support"]
    resistances = [level for level in levels if level.get("zone_type") == "resistance"]
    current_price = float(result.get("current_price") or 0.0)
    nearest_support = (
        min(supports, key=lambda level: abs(float(level["center"]) - current_price))
        if supports
        else None
    )
    nearest_resistance = (
        min(resistances, key=lambda level: abs(float(level["center"]) - current_price))
        if resistances
        else None
    )
    summary_data = result.get("summary") or {}
    nearest = summary_data.get("nearest") or None
    best = summary_data.get("best") or None
    touch_probability = nearest.get("p_touch") if nearest else None
    hold_probability = None
    if nearest:
        hold_probability = nearest.get("p_hold")
        if hold_probability is None:
            hold_probability = nearest.get("event_hold_rate")

    return {
        "dataset_id": summary["id"],
        "symbol": summary["symbol"],
        "title": summary.get("title") or summary["symbol"],
        "timeframe": summary["timeframe"],
        "current_price": result.get("current_price"),
        "change_pct": _change_pct(bars),
        "touch_probability": touch_probability,
        "hold_probability": hold_probability,
        "historical_tests": int(nearest.get("n_events") or 0) if nearest else 0,
        "trend": {
            "label": result.get("trend", {}).get("label") or "未知",
            "detail": result.get("trend", {}).get("detail"),
        },
        "distance_pct": nearest.get("distance_pct") if nearest else None,
        "distance_atr": nearest.get("distance_atr") if nearest else None,
        "nearest_support": nearest_support.get("center") if nearest_support else None,
        "nearest_resistance": (
            nearest_resistance.get("center") if nearest_resistance else None
        ),
        "key_level": best.get("center") if best else None,
        "key_level_type": best.get("zone_type") if best else None,
        "key_level_score": best.get("edge_score") if best else None,
        "bars_used": result.get("bars_used"),
    }


@router.get("/analysis/instruments")
def instrument_summaries(
    db: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    datasets = db.list_datasets()
    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        symbol = str(dataset.get("symbol") or "").strip().upper()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = dataset

    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for dataset in latest_by_symbol.values():
        try:
            record = db.get_dataset(str(dataset["id"]))
            items.append(_summary_item(record, list(record["bars"])))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(
                {
                    "dataset_id": str(dataset.get("id") or ""),
                    "symbol": str(dataset.get("symbol") or ""),
                    "detail": str(exc),
                }
            )

    return {"items": items, "errors": errors, "count": len(items)}


@router.post("/analysis/support-resistance")
def support_resistance(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        lookback = int_in(payload.get("lookback"), 60, 1000, 250)
        result = detect_support_resistance(
            dataset["bars"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            lookback=lookback,
            n_zones=int_in(payload.get("n_zones"), 2, 8, 6),
            direction=str(payload.get("direction") or "both"),
        )
        price_action_result = analyze_price_action(
            dataset["bars"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            lookback=lookback,
            risk_fraction=number_in(payload.get("risk_fraction"), 0.0005, 0.1, 0.01),
            min_rr=number_in(payload.get("min_rr"), 0.5, 10.0, 1.5),
            stance=str(payload.get("stance") or "conservative"),
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
    result["price_action"] = price_action_result
    result["candles"] = dataset["bars"][-150:]
    return result

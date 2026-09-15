from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from xquant.analysis.price_action import analyze_price_action
from xquant.analysis.sr_levels import detect_support_resistance
from xquant.registry import Database

from ..dependencies import get_database, get_dataset_or_404
from ..validation import int_in, number_in

router = APIRouter(tags=["analysis"])

_INSTRUMENTS_CACHE_KEY = "analysis:instruments:snapshot:v1"
_INSTRUMENTS_CACHE_TTL_SECONDS = 300
_FINGERPRINT_FIELDS = (
    "id",
    "symbol",
    "title",
    "timeframe",
    "bar_count",
    "first_session",
    "last_session",
    "last_synced_at",
)


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


def _fingerprint_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _dataset_fingerprint(datasets: list[dict[str, Any]]) -> str:
    metadata = [
        {field: _fingerprint_value(dataset.get(field)) for field in _FINGERPRINT_FIELDS}
        for dataset in datasets
    ]
    metadata.sort(
        key=lambda item: (
            str(item["id"]),
            str(item["symbol"]),
            str(item["timeframe"]),
        )
    )
    encoded = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_snapshot(
    db: Database,
    datasets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
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
    return items, errors


def _snapshot_for_request(
    db: Database,
    datasets: list[dict[str, Any]],
    *,
    refresh: bool,
) -> tuple[dict[str, Any], bool]:
    fingerprint = _dataset_fingerprint(datasets)
    if not refresh:
        cached = db.get_cached_json(_INSTRUMENTS_CACHE_KEY)
        if (
            isinstance(cached, dict)
            and cached.get("fingerprint") == fingerprint
            and isinstance(cached.get("items"), list)
            and isinstance(cached.get("errors"), list)
            and cached.get("generated_at")
        ):
            return cached, True

    items, errors = _build_snapshot(db, datasets)
    snapshot = {
        "fingerprint": fingerprint,
        "items": items,
        "errors": errors,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    db.set_cached_json(
        _INSTRUMENTS_CACHE_KEY,
        snapshot,
        _INSTRUMENTS_CACHE_TTL_SECONDS,
    )
    return snapshot, False


def _filter_items(
    items: list[dict[str, Any]],
    *,
    keyword: str | None,
    timeframe: str | None,
    trend: str | None,
    change: str | None,
) -> list[dict[str, Any]]:
    normalized_keyword = str(keyword or "").strip().casefold()
    filtered: list[dict[str, Any]] = []
    for item in items:
        if normalized_keyword:
            symbol = str(item.get("symbol") or "").casefold()
            title = str(item.get("title") or "").casefold()
            if normalized_keyword not in symbol and normalized_keyword not in title:
                continue
        if timeframe and str(item.get("timeframe") or "") != timeframe:
            continue
        if trend:
            trend_data = item.get("trend")
            label = trend_data.get("label") if isinstance(trend_data, dict) else None
            if label != trend:
                continue
        if change:
            try:
                change_pct = float(item.get("change_pct"))
            except (TypeError, ValueError):
                continue
            if change == "up" and change_pct <= 0:
                continue
            if change == "down" and change_pct >= 0:
                continue
            if change == "flat" and change_pct != 0:
                continue
        filtered.append(item)
    return filtered


def _facets(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    timeframes = sorted(
        {
            str(item["timeframe"])
            for item in items
            if item.get("timeframe") not in (None, "")
        }
    )
    trends = sorted(
        {
            str(item["trend"]["label"])
            for item in items
            if isinstance(item.get("trend"), dict)
            and item["trend"].get("label") not in (None, "")
        }
    )
    return {"timeframes": timeframes, "trends": trends}


@router.get("/analysis/instruments")
def instrument_summaries(
    db: Annotated[Database, Depends(get_database)],
    keyword: Annotated[str | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
    trend: Annotated[str | None, Query()] = None,
    change: Annotated[Literal["up", "down", "flat"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    refresh: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    datasets = db.list_datasets()
    snapshot, from_cache = _snapshot_for_request(db, datasets, refresh=refresh)
    all_items = list(snapshot["items"])
    filtered_items = _filter_items(
        all_items,
        keyword=keyword,
        timeframe=timeframe,
        trend=trend,
        change=change,
    )
    total = len(filtered_items)
    start = (page - 1) * page_size
    page_items = filtered_items[start : start + page_size]
    return {
        "items": page_items,
        "errors": list(snapshot["errors"]),
        "count": len(page_items),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "facets": _facets(all_items),
        "from_cache": from_cache,
        "generated_at": snapshot["generated_at"],
    }


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

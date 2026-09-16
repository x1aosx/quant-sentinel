from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from xquant.ai.context_builder import build_quant_analysis_context, run_quant_analysis
from xquant.ai.service import normalize_ai_settings
from xquant.quant.core.models import MultiTimeframeProfile
from xquant.quant.decision.engine import QuantDecisionEngine
from xquant.quant.multi_timeframe.analyzer import MultiTimeframeAnalyzer
from xquant.quant.single_timeframe.analyzer import SingleTimeframeAnalyzer
from xquant.registry import Database
from xquant.system_config import SystemConfigStore

from ..dependencies import get_database, get_system_config

router = APIRouter(prefix="/stocks", tags=["stock-analysis"])

_ANALYSIS_VERSION = "stock-analysis-v1"
_SINGLE_TIMEFRAME_VERSION = "single-timeframe-v1"
_MULTI_TIMEFRAME_VERSION = "multi-timeframe-v1"
_DECISION_VERSION = "quant-decision-v1"
_DEFAULT_PROFILE = {
    "strategic": "1d",
    "tactical": "1h",
    "confirmation": "30m",
    "execution": "15m",
}
_CACHE_TTL_SECONDS = 900


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    return symbol


def _normalize_profile(payload: Mapping[str, Any] | None = None) -> MultiTimeframeProfile:
    source = dict(payload or {})
    profile = {
        role: str(source.get(role) or default).strip().lower()
        for role, default in _DEFAULT_PROFILE.items()
    }
    if len(set(profile.values())) != len(profile):
        raise HTTPException(status_code=400, detail="多周期角色映射不能使用重复周期")
    return MultiTimeframeProfile(**profile)


def _profile_dict(profile: MultiTimeframeProfile) -> dict[str, str]:
    return {
        "strategic": profile.strategic,
        "tactical": profile.tactical,
        "confirmation": profile.confirmation,
        "execution": profile.execution,
    }


def _dataset_fingerprint(datasets: list[dict[str, Any]]) -> str:
    fields = (
        "id",
        "symbol",
        "title",
        "timeframe",
        "bar_count",
        "first_session",
        "last_session",
        "last_synced_at",
    )
    normalized = [
        {
            field: str(dataset.get(field)) if dataset.get(field) is not None else None
            for field in fields
        }
        for dataset in datasets
    ]
    normalized.sort(key=lambda item: (item["symbol"] or "", item["timeframe"] or ""))
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _symbol_datasets(db: Database, symbol: str) -> list[dict[str, Any]]:
    matches = [
        dataset
        for dataset in db.list_datasets()
        if str(dataset.get("symbol") or "").strip().upper() == symbol
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="股票不存在或尚未同步行情")
    return matches


def _dataset_by_timeframe(datasets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        timeframe = str(dataset.get("timeframe") or "").strip().lower()
        if timeframe and timeframe not in result:
            result[timeframe] = dataset
    return result


def _change_pct(bars: list[dict[str, Any]]) -> float | None:
    if len(bars) < 2:
        return None
    previous = float(bars[-2].get("close") or 0.0)
    current = float(bars[-1].get("close") or 0.0)
    if previous == 0.0:
        return None
    return round((current - previous) / previous * 100.0, 4)


def _quote_from_record(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {
            "current_price": None,
            "change_pct": None,
            "last_session": None,
            "timeframe": None,
        }
    bars = list(record.get("bars") or [])
    current = float(bars[-1].get("close") or 0.0) if bars else None
    return {
        "current_price": current,
        "change_pct": _change_pct(bars),
        "last_session": str(bars[-1].get("session_id")) if bars else None,
        "timeframe": str((record.get("summary") or {}).get("timeframe") or "") or None,
    }


def _base_cache_key(symbol: str, fingerprint: str, profile: MultiTimeframeProfile) -> str:
    profile_key = "-".join(
        f"{role}:{timeframe}" for role, timeframe in _profile_dict(profile).items()
    )
    return f"stock-analysis:{symbol}:{fingerprint}:{profile_key}:v1"


def _ai_cache_key(symbol: str, fingerprint: str, profile: MultiTimeframeProfile) -> str:
    return f"stock-analysis-ai:{symbol}:{fingerprint}:{_base_cache_key(symbol, fingerprint, profile)}"


def _build_payload(
    db: Database,
    *,
    symbol: str,
    datasets: list[dict[str, Any]],
    profile: MultiTimeframeProfile,
) -> dict[str, Any]:
    datasets_by_timeframe = _dataset_by_timeframe(datasets)
    records: dict[str, dict[str, Any]] = {}
    timeframe_results: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    analyzer = SingleTimeframeAnalyzer()

    for timeframe in dict.fromkeys(_profile_dict(profile).values()):
        dataset = datasets_by_timeframe.get(timeframe)
        if not dataset:
            errors.append({"timeframe": timeframe, "detail": "缺少该周期数据集"})
            continue
        try:
            record = db.get_dataset(str(dataset["id"]))
            records[timeframe] = record
            result = analyzer.analyze(
                symbol=symbol,
                timeframe=timeframe,
                bars=list(record.get("bars") or []),
                context={"dataset_id": str(dataset["id"])},
            )
            result.raw_result["candles"] = list(record.get("bars") or [])[-150:]
            timeframe_results[timeframe] = result
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"timeframe": timeframe, "detail": str(exc)})

    if not timeframe_results:
        raise HTTPException(status_code=422, detail="没有可用于分析的周期数据")

    multi_timeframe = MultiTimeframeAnalyzer().analyze(
        symbol,
        timeframe_results,
        profile,
    )
    decision = QuantDecisionEngine().evaluate(multi_timeframe, timeframe_results)
    quote_record = records.get(profile.execution) or records.get(profile.strategic)
    first_dataset = datasets[0]
    generated_at = datetime.now(UTC).isoformat()
    timeframe_payload = {
        timeframe: result.to_dict() for timeframe, result in timeframe_results.items()
    }
    return _json_safe(
        {
            "symbol": symbol,
            "name": str(first_dataset.get("title") or symbol),
            "quote": _quote_from_record(quote_record),
            "timeframes": timeframe_payload,
            "multi_timeframe": multi_timeframe.to_dict(),
            "decision": decision.to_dict(),
            "ai_summary": None,
            "errors": errors,
            "generated_at": generated_at,
            "from_cache": False,
            "versions": {
                "analysis": _ANALYSIS_VERSION,
                "single_timeframe": _SINGLE_TIMEFRAME_VERSION,
                "multi_timeframe": _MULTI_TIMEFRAME_VERSION,
                "decision": _DECISION_VERSION,
            },
        }
    )


def _with_ai(
    payload: Mapping[str, Any],
    store: SystemConfigStore,
    *,
    overrides: Mapping[str, Any] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    result = dict(payload)
    if use_cache and result.get("ai_summary"):
        return result
    settings_payload = store.merge_provider_payload(dict(overrides or {}))
    settings = normalize_ai_settings(settings_payload)
    context = build_quant_analysis_context(
        symbol=str(result.get("symbol") or ""),
        name=str(result.get("name") or ""),
        quote=result.get("quote") if isinstance(result.get("quote"), Mapping) else None,
        timeframes=result.get("timeframes") or {},
        multi_timeframe=result.get("multi_timeframe") or {},
        decision=result.get("decision") or {},
        generated_at=str(result.get("generated_at") or ""),
    )
    result["ai_summary"] = run_quant_analysis(context, settings)
    return result


def _get_or_build(
    db: Database,
    *,
    symbol: str,
    profile: MultiTimeframeProfile,
    refresh: bool,
) -> tuple[dict[str, Any], str]:
    datasets = _symbol_datasets(db, symbol)
    fingerprint = _dataset_fingerprint(datasets)
    cache_key = _base_cache_key(symbol, fingerprint, profile)
    if not refresh:
        cached = db.get_cached_json(cache_key)
        if isinstance(cached, dict) and cached.get("symbol") == symbol:
            cached["from_cache"] = True
            return cached, fingerprint

    payload = _build_payload(
        db,
        symbol=symbol,
        datasets=datasets,
        profile=profile,
    )
    db.set_cached_json(cache_key, payload, _CACHE_TTL_SECONDS)
    return payload, fingerprint


@router.get("/{symbol}/analysis")
def stock_analysis(
    symbol: str,
    db: Annotated[Database, Depends(get_database)],
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    strategic: Annotated[str, Query()] = "1d",
    tactical: Annotated[str, Query()] = "1h",
    confirmation: Annotated[str, Query()] = "30m",
    execution: Annotated[str, Query()] = "15m",
    refresh: Annotated[bool, Query()] = False,
    include_ai: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    profile = _normalize_profile(
        {
            "strategic": strategic,
            "tactical": tactical,
            "confirmation": confirmation,
            "execution": execution,
        }
    )
    payload, fingerprint = _get_or_build(
        db,
        symbol=normalized,
        profile=profile,
        refresh=refresh,
    )
    if not include_ai:
        return payload
    ai_cache_key = _ai_cache_key(normalized, fingerprint, profile)
    cached_ai = None if refresh else db.get_cached_json(ai_cache_key)
    if isinstance(cached_ai, dict):
        payload["ai_summary"] = cached_ai
        return payload
    result = _with_ai(payload, store, use_cache=False)
    if isinstance(result.get("ai_summary"), dict):
        db.set_cached_json(ai_cache_key, result["ai_summary"], _CACHE_TTL_SECONDS)
    return result


@router.get("/{symbol}/analysis/timeframes")
def stock_timeframes(
    symbol: str,
    db: Annotated[Database, Depends(get_database)],
    strategic: Annotated[str, Query()] = "1d",
    tactical: Annotated[str, Query()] = "1h",
    confirmation: Annotated[str, Query()] = "30m",
    execution: Annotated[str, Query()] = "15m",
    refresh: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    profile = _normalize_profile(
        {
            "strategic": strategic,
            "tactical": tactical,
            "confirmation": confirmation,
            "execution": execution,
        }
    )
    payload, _ = _get_or_build(
        db,
        symbol=normalized,
        profile=profile,
        refresh=refresh,
    )
    return {
        "symbol": payload["symbol"],
        "profile": _profile_dict(profile),
        "items": payload["timeframes"],
        "missing_timeframes": payload["multi_timeframe"].get("missing_timeframes", []),
        "generated_at": payload["generated_at"],
    }


@router.get("/{symbol}/analysis/timeframes/{timeframe}")
def stock_timeframe(
    symbol: str,
    timeframe: str,
    db: Annotated[Database, Depends(get_database)],
    refresh: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    requested = str(timeframe or "").strip().lower()
    datasets = _symbol_datasets(db, normalized)
    dataset = _dataset_by_timeframe(datasets).get(requested)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"股票 {normalized} 缺少 {requested} 周期数据")
    record = db.get_dataset(str(dataset["id"]))
    result = SingleTimeframeAnalyzer().analyze(
        symbol=normalized,
        timeframe=requested,
        bars=list(record.get("bars") or []),
        context={"dataset_id": str(dataset["id"]), "refresh": refresh},
    )
    return result.to_dict()


@router.post("/{symbol}/analysis/multi-timeframe")
def stock_multi_timeframe(
    symbol: str,
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    request_payload = payload or {}
    profile_payload = request_payload.get("profile")
    profile = _normalize_profile(
        profile_payload if isinstance(profile_payload, Mapping) else request_payload
    )
    result, _ = _get_or_build(
        db,
        symbol=normalized,
        profile=profile,
        refresh=bool(request_payload.get("refresh", False)),
    )
    return {
        "symbol": result["symbol"],
        "multi_timeframe": result["multi_timeframe"],
        "decision": result["decision"],
        "timeframes": result["timeframes"],
        "generated_at": result["generated_at"],
    }


@router.post("/{symbol}/analysis/ai")
def stock_ai_analysis(
    symbol: str,
    db: Annotated[Database, Depends(get_database)],
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    request_payload = payload or {}
    profile = _normalize_profile(
        request_payload.get("profile")
        if isinstance(request_payload.get("profile"), Mapping)
        else request_payload
    )
    base, fingerprint = _get_or_build(
        db,
        symbol=normalized,
        profile=profile,
        refresh=bool(request_payload.get("refresh", False)),
    )
    result = _with_ai(base, store, overrides=request_payload, use_cache=False)
    if isinstance(result.get("ai_summary"), dict):
        db.set_cached_json(
            _ai_cache_key(normalized, fingerprint, profile),
            result["ai_summary"],
            _CACHE_TTL_SECONDS,
        )
    return result["ai_summary"]

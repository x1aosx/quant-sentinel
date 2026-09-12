from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from xquant.ai.coordinator import MonitorManager
from xquant.ai.service import (
    build_followup_prompt,
    build_snapshot,
    call_chat_completion,
    mask_provider,
    normalize_ai_settings,
    run_two_stage,
    stream_two_stage,
)
from xquant.registry import Database
from xquant.system_config import SystemConfigStore

from ..dependencies import get_database, get_dataset_or_404, get_monitor, get_system_config

router = APIRouter(tags=["ai"])


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _sse_payload(event: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _json_safe(event),
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return f"data: {payload}\n\n"


def _merged_payload(
    store: SystemConfigStore,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    return store.merge_provider_payload(payload or {})


def _persist_record(
    db: Database,
    record: Mapping[str, Any],
    *,
    dataset_id: str | None = None,
) -> dict[str, Any] | None:
    save = getattr(db, "save_analysis_record", None)
    if not callable(save):
        return None
    return save(dict(record), dataset_id=dataset_id)


@router.post("/ai/config")
def ai_config(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = normalize_ai_settings(_merged_payload(store, payload))
    return {
        "status": "ok",
        "provider": mask_provider(settings.provider.__dict__),
        "analysis": {
            "analysis_bar_count": settings.analysis_bar_count,
            "decision_stance": settings.decision_stance,
            "enable_next_bar_prediction": settings.enable_next_bar_prediction,
            "keep_analysis": settings.keep_analysis,
            "incremental_max_new_bars": settings.incremental_max_new_bars,
        },
        "analysis_bar_count": settings.analysis_bar_count,
    }


@router.post("/ai/analyze")
def analyze_ai(
    db: Annotated[Database, Depends(get_database)],
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        ai_settings = normalize_ai_settings(_merged_payload(store, payload))
        snapshot = build_snapshot(
            dataset_id=dataset["summary"]["id"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            bars=dataset["bars"],
            settings=ai_settings,
        )
        record = run_two_stage(snapshot, ai_settings)
        _persist_record(db, record, dataset_id=str(dataset["summary"]["id"]))
        return record
    except (TypeError, ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/analyze/stream")
def analyze_ai_stream(
    db: Annotated[Database, Depends(get_database)],
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> StreamingResponse:
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        ai_settings = normalize_ai_settings(_merged_payload(store, payload))
        snapshot = build_snapshot(
            dataset_id=dataset["summary"]["id"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            bars=dataset["bars"],
            settings=ai_settings,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def event_stream():
        try:
            for event in stream_two_stage(snapshot, ai_settings):
                if event.get("type") == "done" and isinstance(event.get("record"), Mapping):
                    _persist_record(
                        db,
                        event["record"],
                        dataset_id=str(dataset["summary"]["id"]),
                    )
                yield _sse_payload(event)
        except Exception as exc:
            # The SSE response has already started, so failures must be delivered as events.
            error_record = {
                "id": f"ai-stream-{dataset['summary']['id']}",
                "status": "error",
                "symbol": snapshot.get("symbol"),
                "timeframe": snapshot.get("timeframe"),
                "exception": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "stage": "stream",
                },
            }
            yield _sse_payload({"type": "error", "message": str(exc), "record": error_record})
            yield _sse_payload({"type": "done", "record": error_record})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ai/followup")
def followup_ai(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    record = payload.get("record")
    question = str(payload.get("question") or "").strip()
    if not record or not question:
        raise HTTPException(status_code=400, detail="追问需要 record 与 question")
    ai_settings = normalize_ai_settings(_merged_payload(store, payload))
    messages = build_followup_prompt(record, question)
    if not ai_settings.provider.api_key:
        return {"status": "ok", "answer": "本地研究模式暂不支持模型追问。请配置 API Key 后使用。"}
    try:
        return {"status": "ok", "answer": call_chat_completion(ai_settings.provider, messages)}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/batch/analyze")
def batch_analyze(
    db: Annotated[Database, Depends(get_database)],
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise HTTPException(status_code=400, detail="批量分析需要 targets 列表")
    try:
        from xquant.ai.coordinator import BatchAnalyzer

        merged = _merged_payload(store, payload)
        result = BatchAnalyzer(db).analyze(
            targets,
            merged,
            concurrency=payload.get("concurrency"),
        )
        for item in result.get("items", []):
            record = item.get("record")
            dataset_summary = item.get("dataset")
            if isinstance(record, Mapping):
                _persist_record(
                    db,
                    record,
                    dataset_id=(
                        str(dataset_summary.get("id"))
                        if isinstance(dataset_summary, Mapping) and dataset_summary.get("id")
                        else None
                    ),
                )
        return result
    except (TypeError, ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/monitor/start")
def start_monitor(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    monitor: Annotated[MonitorManager, Depends(get_monitor)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise HTTPException(status_code=400, detail="盯盘需要 targets 列表")
    try:
        merged = _merged_payload(store, payload)
        return monitor.start(
            targets,
            merged,
            interval_seconds=payload.get("interval_seconds"),
            auto_notify=bool(payload.get("auto_notify", False)),
            schedule=payload.get("monitor_schedule") or merged.get("monitor_schedule"),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/monitor/stop")
def stop_monitor(
    monitor: Annotated[MonitorManager, Depends(get_monitor)],
) -> dict[str, Any]:
    return monitor.stop()


@router.get("/ai/monitor/status")
def monitor_status(
    monitor: Annotated[MonitorManager, Depends(get_monitor)],
) -> dict[str, Any]:
    return monitor.status()


@router.post("/ai/monitor/run-once")
def run_monitor_once(
    monitor: Annotated[MonitorManager, Depends(get_monitor)],
) -> dict[str, Any]:
    return monitor.run_once()


@router.get("/ai/records")
def list_analysis_records(
    db: Annotated[Database, Depends(get_database)],
    dataset_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    return {"items": db.list_analysis_records(dataset_id=dataset_id, limit=limit)}


@router.get("/ai/records/{record_id}")
def get_analysis_record(
    record_id: str,
    db: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    try:
        return db.get_analysis_record(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="分析记录不存在") from exc

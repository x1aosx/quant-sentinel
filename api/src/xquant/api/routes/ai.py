from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
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


def _merged_payload(
    store: SystemConfigStore,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    return store.merge_provider_payload(payload or {})


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
        return run_two_stage(snapshot, ai_settings)
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
        for event in stream_two_stage(snapshot, ai_settings):
            yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
        return BatchAnalyzer(db).analyze(
            targets,
            merged,
            concurrency=payload.get("concurrency"),
        )
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

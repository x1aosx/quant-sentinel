from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from xquant.ai.service import (
    build_followup_prompt,
    build_snapshot,
    build_stage1_prompt,
    build_stage2_prompt,
    call_chat_completion,
    mask_provider,
    normalize_ai_settings,
    run_two_stage,
    stream_chat_completion,
)
from xquant.registry import Database

from ..dependencies import get_database, get_dataset_or_404

router = APIRouter(tags=["ai"])


@router.post("/ai/config")
def ai_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = normalize_ai_settings(payload)
    return {
        "status": "ok",
        "provider": mask_provider(settings.provider.__dict__),
        "analysis_bar_count": settings.analysis_bar_count,
    }


@router.post("/ai/analyze")
def analyze_ai(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        ai_settings = normalize_ai_settings(payload)
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
async def analyze_ai_stream(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
):
    payload = payload or {}
    dataset = get_dataset_or_404(db, str(payload.get("dataset_id") or ""))
    try:
        ai_settings = normalize_ai_settings(payload)
        snapshot = build_snapshot(
            dataset_id=dataset["summary"]["id"],
            symbol=dataset["summary"]["symbol"],
            timeframe=dataset["summary"]["timeframe"],
            bars=dataset["bars"],
            settings=ai_settings,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def event_stream():
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "snapshot",
                    "symbol": snapshot["symbol"],
                    "timeframe": snapshot["timeframe"],
                    "bar_count": len(snapshot["candles"]),
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
        if not ai_settings.provider.api_key:
            yield f"data: {json.dumps({'type': 'log', 'text': '进入本地研究模式。'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'log', 'text': '使用确定性规则完成诊断与决策。'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return
        stage1_messages = build_stage1_prompt(snapshot)
        for chunk in stream_chat_completion(ai_settings.provider, stage1_messages):
            yield f"data: {json.dumps({'type': 'stage1', 'text': chunk}, ensure_ascii=False)}\n\n"
        stage2_messages = build_stage2_prompt(snapshot, {})
        for chunk in stream_chat_completion(ai_settings.provider, stage2_messages):
            yield f"data: {json.dumps({'type': 'stage2', 'text': chunk}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/ai/followup")
def followup_ai(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    record = payload.get("record")
    question = str(payload.get("question") or "").strip()
    if not record or not question:
        raise HTTPException(status_code=400, detail="追问需要 record 与 question")
    ai_settings = normalize_ai_settings(payload)
    messages = build_followup_prompt(record, question)
    if not ai_settings.provider.api_key:
        return {"status": "ok", "answer": "本地研究模式暂不支持模型追问。请配置 API Key 后使用。"}
    try:
        return {"status": "ok", "answer": call_chat_completion(ai_settings.provider, messages)}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

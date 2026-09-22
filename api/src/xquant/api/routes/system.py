from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from xquant.ai.service import normalize_ai_settings, test_provider_connection
from xquant.notifications.feishu import send_feishu_message
from xquant.system_config import SystemConfigStore

from ..dependencies import get_system_config

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/config")
def get_config(store: Annotated[SystemConfigStore, Depends(get_system_config)]) -> dict[str, Any]:
    return {"status": "ok", "config": store.public_payload()}


@router.put("/config")
def update_config(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        store.update(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "config": store.public_payload()}


@router.post("/config/reset")
def reset_config(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
) -> dict[str, Any]:
    store.reset()
    return {"status": "ok", "config": store.public_payload()}


@router.post("/config/test-provider")
def test_provider(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the configured model endpoint with one minimal chat request."""

    body = payload or {}
    try:
        settings = normalize_ai_settings(store.merge_provider_payload(body))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = test_provider_connection(settings.provider, prompt=body.get("prompt"))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"大模型连接测试失败：{exc}") from exc
    return {"status": "ok", **result}


@router.post("/config/test-feishu")
def test_feishu(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    settings = store.settings.feishu
    result = body.get("record")
    if not isinstance(result, dict):
        result = {
            "symbol": body.get("symbol") or "TEST",
            "timeframe": body.get("timeframe") or "1d",
            "stage1_diagnosis": {
                "diagnosis_summary": "这是一条 X-Quant 飞书通知测试消息。",
            },
            "stage2_decision": {
                "decision": {
                    "action": "WAIT",
                    "confidence": 0,
                    "reasoning": "测试消息，不构成投资建议。",
                },
                "future_trend": {"label": "测试"},
                "next_cycle_prediction": {"cycle": "unknown"},
                "next_bar_prediction": {"direction": "neutral"},
            },
        }
    try:
        return send_feishu_message(
            result,
            webhook_url=settings.webhook_url,
            secret=settings.secret,
            enabled=settings.enabled,
            notify_on_order_only=False,
            confidence_threshold=0,
            proxy_url=store.settings.provider.proxy_url,
            timeout_seconds=store.settings.provider.timeout_seconds,
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

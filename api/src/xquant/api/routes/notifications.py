from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from xquant.notifications.feishu import send_feishu_message
from xquant.system_config import SystemConfigStore

from ..dependencies import get_system_config

router = APIRouter(tags=["notifications"])


@router.post("/notifications/feishu")
def notify_feishu(
    store: Annotated[SystemConfigStore, Depends(get_system_config)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    result = payload.get("record") or payload.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="缺少通知内容 record")
    settings = store.settings.feishu
    try:
        return send_feishu_message(
            result,
            webhook_url=str(payload.get("webhook_url") or settings.webhook_url),
            secret=str(payload.get("secret") or settings.secret),
            enabled=bool(payload.get("enabled", settings.enabled)),
            notify_on_order_only=bool(
                payload.get("notify_on_order_only", settings.notify_on_order_only)
            ),
            confidence_threshold=int(
                payload.get("confidence_threshold", settings.confidence_threshold)
            ),
            proxy_url=store.settings.provider.proxy_url,
            timeout_seconds=store.settings.provider.timeout_seconds,
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

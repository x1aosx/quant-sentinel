from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from xquant.notifications.feishu import send_feishu_message

router = APIRouter(tags=["notifications"])


@router.post("/notifications/feishu")
def notify_feishu(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    result = payload.get("record") or payload.get("result")
    if not result:
        raise HTTPException(status_code=400, detail="缺少通知内容 record")
    try:
        return send_feishu_message(
            result,
            webhook_url=str(payload.get("webhook_url") or ""),
            secret=str(payload.get("secret") or ""),
            enabled=bool(payload.get("enabled", True)),
            notify_on_order_only=bool(payload.get("notify_on_order_only", False)),
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

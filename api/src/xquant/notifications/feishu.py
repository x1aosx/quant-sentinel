from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Mapping

import httpx


def build_feishu_card(result: Mapping[str, Any]) -> dict[str, Any]:
    stage2 = result.get("stage2_decision") or {}
    decision = stage2.get("decision") if isinstance(stage2.get("decision"), dict) else {}
    next_cycle = stage2.get("next_cycle_prediction") or {}
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "X-Quant 研究决策通知"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**品种**：{result.get('symbol', '--')}  \n"
                        f"**周期**：{result.get('timeframe', '--')}  \n"
                        f"**方向**：{decision.get('action', decision.get('order_type', 'WAIT'))}  \n"
                        f"**置信度**：{decision.get('confidence', '--')}"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": f"**决策理由**\n{decision.get('reasoning', '未提供')}",
                },
                {
                    "tag": "markdown",
                    "content": f"**下一周期预期**\n{next_cycle.get('cycle', '未提供')}",
                },
                {
                    "tag": "markdown",
                    "content": "本结果仅供研究/模拟参考，不构成投资建议，不连接券商或执行下单。",
                },
            ],
        },
    }


def sign_feishu_payload(payload: dict[str, Any], secret: str) -> dict[str, Any]:
    if not secret:
        return payload
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return {**payload, "timestamp": timestamp, "sign": base64.b64encode(digest).decode("utf-8")}


def send_feishu_message(
    result: Mapping[str, Any],
    *,
    webhook_url: str,
    secret: str = "",
    enabled: bool = True,
    notify_on_order_only: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    if not enabled:
        return {"sent": False, "reason": "飞书通知未启用"}
    if not webhook_url:
        return {"sent": False, "reason": "缺少 webhook_url"}
    decision = (result.get("stage2_decision") or {}).get("decision", {})
    if notify_on_order_only and str(decision.get("action", decision.get("order_type", ""))).upper() in {
        "WAIT",
        "不下单",
    }:
        return {"sent": False, "reason": "仅交易意图通知，但当前为观望"}
    payload = sign_feishu_payload(build_feishu_card(result), secret)
    if transport is not None:
        response = transport(webhook_url, payload)
    else:
        response = httpx.post(webhook_url, json=payload, timeout=12.0)
    try:
        body = response.json()
    except Exception as exc:
        return {"sent": False, "reason": f"飞书响应解析失败: {exc}"}
    success = int(body.get("code", -1)) == 0 or int(body.get("StatusCode", -1)) == 0
    return {"sent": success, "response": body, "payload_masked": {"msg_type": payload.get("msg_type")}}

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _mask_webhook(value: str) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "***"
    path = parts.path.rstrip("/")
    if path:
        path = f"{path.rsplit('/', 1)[0]}/***"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _action_label(decision: Mapping[str, Any]) -> str:
    action = str(
        _first(
            decision,
            "action",
            "order_type",
            "order_direction",
            default="WAIT",
        )
    ).upper()
    return {
        "BUY": "LONG",
        "LONG": "LONG",
        "做多": "LONG",
        "SELL": "SHORT",
        "SHORT": "SHORT",
        "做空": "SHORT",
        "HOLD": "WAIT",
        "NONE": "WAIT",
        "不下单": "WAIT",
        "观望": "WAIT",
    }.get(action, action)


def build_feishu_card(result: Mapping[str, Any]) -> dict[str, Any]:
    stage2 = result.get("stage2_decision")
    if not isinstance(stage2, Mapping):
        stage2 = {}
    decision = stage2.get("decision")
    if not isinstance(decision, Mapping):
        decision = {}
    diagnosis = result.get("stage1_diagnosis")
    if not isinstance(diagnosis, Mapping):
        diagnosis = {}
    future = stage2.get("future_trend")
    if not isinstance(future, Mapping):
        future = result.get("future_trend") if isinstance(result.get("future_trend"), Mapping) else {}
    next_cycle = stage2.get("next_cycle_prediction")
    if not isinstance(next_cycle, Mapping):
        next_cycle = {}
    next_bar = stage2.get("next_bar_prediction")
    if not isinstance(next_bar, Mapping):
        next_bar = {}
    action = _action_label(decision)
    confidence = _first(decision, "confidence", "trade_confidence", default="--")
    entry = _first(decision, "entry", "entry_price", default="--")
    stop = _first(decision, "stop", "stop_loss", "stop_loss_price", default="--")
    target = _first(
        decision,
        "target",
        "take_profit",
        "take_profit_price",
        "target_price",
        default="--",
    )
    rr = _first(decision, "rr", "risk_reward", "risk_reward_ratio", default="--")
    reasoning = _first(decision, "reasoning", "summary", default="未提供")
    invalidation = _first(decision, "invalidation", "invalid_condition", default="未提供")
    summary = _first(
        diagnosis,
        "diagnosis_summary",
        "summary",
        default=stage2.get("diagnosis_summary", "未提供"),
    )
    cycle = _first(
        next_cycle,
        "cycle",
        "label",
        "prediction",
        default=diagnosis.get("next_cycle", "未提供"),
    )
    trend = _first(future, "label", "summary", "direction", default="未提供")
    bar_direction = _first(next_bar, "direction", "label", default="未提供")
    template = "green" if action == "LONG" else "red" if action == "SHORT" else "blue"
    watch_points = _as_list(decision.get("watch_points") or decision.get("key_factors"))
    risk_flags = _as_list(decision.get("risk_flags") or decision.get("risks"))
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": (
                f"**品种**：{result.get('symbol', '--')}  **周期**：{result.get('timeframe', '--')}  \n"
                f"**决策**：{action}  **置信度**：{confidence}  \n"
                f"**入场**：{entry}  **止损**：{stop}  **目标**：{target}  **RR**：{rr}"
            ),
        },
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**诊断摘要**\n{summary}"},
        {"tag": "markdown", "content": f"**决策理由**\n{reasoning}"},
        {"tag": "markdown", "content": f"**失效条件**\n{invalidation}"},
        {
            "tag": "markdown",
            "content": (
                f"**未来走势**：{trend}  \n"
                f"**下一周期**：{cycle}  \n"
                f"**下一根K线**：{bar_direction}"
            ),
        },
    ]
    if watch_points:
        elements.append(
            {
                "tag": "markdown",
                "content": "**关注点**\n" + "\n".join(f"- {item}" for item in watch_points[:5]),
            }
        )
    if risk_flags:
        elements.append(
            {
                "tag": "markdown",
                "content": "**风险**\n" + "\n".join(f"- {item}" for item in risk_flags[:5]),
            }
        )
    if result.get("exception"):
        elements.append(
            {
                "tag": "markdown",
                "content": f"**异常**\n{result.get('exception')}",
            }
        )
    elements.append(
        {
            "tag": "markdown",
            "content": "本结果仅供研究/模拟参考，不构成投资建议，不连接券商或执行下单。",
        }
    )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"X-Quant 决策通知 - {result.get('symbol', '--')}",
                },
                "template": template,
            },
            "elements": elements,
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
    confidence_threshold: int = 0,
    proxy_url: str = "",
    timeout_seconds: float = 12.0,
    transport: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"sent": False, "reason": "飞书通知未启用"}
    if not webhook_url:
        return {"sent": False, "reason": "缺少 webhook_url"}
    stage2 = result.get("stage2_decision")
    decision = stage2.get("decision") if isinstance(stage2, Mapping) else {}
    if not isinstance(decision, Mapping):
        decision = {}
    action = _action_label(decision)
    if notify_on_order_only and action == "WAIT":
        return {"sent": False, "reason": "仅交易意图通知，但当前为观望"}
    try:
        confidence = float(_first(decision, "confidence", "trade_confidence", default=0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < confidence_threshold:
        return {
            "sent": False,
            "reason": f"置信度低于通知阈值 {confidence_threshold}",
            "confidence": confidence,
        }
    payload = sign_feishu_payload(build_feishu_card(result), secret)
    if transport is not None:
        response = transport(webhook_url, payload)
    else:
        with httpx.Client(timeout=timeout_seconds, proxy=proxy_url or None) as client:
            response = client.post(webhook_url, json=payload)
    try:
        body = response.json()
    except (TypeError, ValueError) as exc:
        return {"sent": False, "reason": f"飞书响应解析失败: {exc}"}
    success = int(body.get("code", -1)) == 0 or int(body.get("StatusCode", -1)) == 0
    return {
        "sent": success,
        "response": body,
        "payload_summary": {
            "msg_type": payload.get("msg_type"),
            "symbol": result.get("symbol"),
            "timeframe": result.get("timeframe"),
            "action": action,
            "webhook": _mask_webhook(webhook_url),
        },
    }

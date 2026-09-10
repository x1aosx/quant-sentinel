from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

import httpx

from ..analysis.price_action import analyze_price_action
from ..analysis.sr_levels import detect_support_resistance


@dataclass(frozen=True)
class AIProviderSettings:
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    thinking: bool = False
    reasoning_effort: str = "medium"
    context_window: int = 128000


@dataclass(frozen=True)
class AISettings:
    provider: AIProviderSettings
    analysis_bar_count: int = 120
    decision_stance: str = "balanced"
    enable_next_bar_prediction: bool = True


@dataclass(frozen=True)
class AnalysisSnapshot:
    dataset_id: str
    symbol: str
    timeframe: str
    bars: Sequence[Mapping[str, Any]]
    lookback: int = 120
    decision_stance: str = "balanced"
    enable_next_bar_prediction: bool = True


def normalize_ai_settings(payload: Mapping[str, Any] | None = None) -> AISettings:
    data = dict(payload or {})
    provider_data = dict(data.get("provider") or {})
    provider = AIProviderSettings(
        model=str(provider_data.get("model") or "deepseek-chat"),
        base_url=str(provider_data.get("base_url") or "https://api.deepseek.com/v1").rstrip("/"),
        api_key=str(provider_data.get("api_key") or provider_data.get("api_key_env") or ""),
        thinking=bool(provider_data.get("thinking") or False),
        reasoning_effort=str(provider_data.get("reasoning_effort") or "medium"),
        context_window=int(provider_data.get("context_window") or 128000),
    )
    return AISettings(
        provider=provider,
        analysis_bar_count=max(60, min(1000, int(data.get("analysis_bar_count") or 120))),
        decision_stance=str(data.get("decision_stance") or "balanced"),
        enable_next_bar_prediction=bool(data.get("enable_next_bar_prediction", True)),
    )


def mask_provider(provider: Mapping[str, Any]) -> dict[str, Any]:
    api_key = str(provider.get("api_key") or "")
    masked = "***" if api_key else ""
    return {
        "model": provider.get("model", ""),
        "base_url": provider.get("base_url", ""),
        "api_key": masked,
        "thinking": bool(provider.get("thinking")),
        "reasoning_effort": provider.get("reasoning_effort", ""),
        "context_window": provider.get("context_window", 0),
    }


def build_snapshot(
    *,
    dataset_id: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[Mapping[str, Any]],
    settings: AISettings,
) -> dict[str, Any]:
    selected = list(bars)[-settings.analysis_bar_count :]
    if len(selected) < 60:
        raise ValueError("AI 快照至少需要 60 根已收盘K线")
    sr = detect_support_resistance(
        selected,
        symbol=symbol,
        timeframe=timeframe,
        lookback=len(selected),
        n_zones=6,
    )
    pa = analyze_price_action(
        selected,
        symbol=symbol,
        timeframe=timeframe,
        lookback=len(selected),
        stance=settings.decision_stance,
    )
    return {
        "dataset_id": dataset_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": pa["current_price"],
        "trend": pa["market_context"],
        "support_resistance": {
            "supports": sr.get("levels", []) if sr.get("levels") else [],
            "resistances": [level for level in sr.get("levels", []) if level.get("zone_type") == "resistance"],
            "supports_only": [level for level in sr.get("levels", []) if level.get("zone_type") == "support"],
        },
        "features": pa.get("features", {}),
        "decision": pa.get("decision", {}),
        "candles": list(selected),
        "analysis_bar_count": len(selected),
    }


def _snapshot_table(snapshot: Mapping[str, Any]) -> str:
    rows = []
    for index, bar in enumerate(reversed(list(snapshot.get("candles", []))), start=1):
        rows.append(
            f"K{index}: {bar.get('session_id')} O={bar.get('open')} H={bar.get('high')} "
            f"L={bar.get('low')} C={bar.get('close')} V={bar.get('volume')}"
        )
    return "\n".join(rows)


def build_stage1_prompt(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    system = (
        "你是严谨的量化研究助手。只基于给定的已收盘K线做市场诊断，不给出投资建议，不执行交易。"
        "必须输出 JSON，字段包括 current_trend, current_cycle, next_cycle, supports, resistances, "
        "diagnosis_summary, key_factors, confidence。"
    )
    levels = snapshot.get("support_resistance", {})
    supports = ", ".join(str(level.get("center")) for level in levels.get("supports_only", []))
    resistances = ", ".join(str(level.get("center")) for level in levels.get("resistances", []))
    user = (
        f"品种 {snapshot.get('symbol')}，周期 {snapshot.get('timeframe')}。\n"
        f"当前趋势：{snapshot.get('trend')}\n"
        f"当前周期位置：{snapshot.get('trend', {}).get('cycle_position', '未知')}\n"
        f"候选支撑：{supports or '无'}\n候选阻力：{resistances or '无'}\n"
        "请先识别当前趋势、当前时长周期、下一个时长周期、支撑区、阻力区与关键结构。\n"
        "K线（旧到新）：\n" + _snapshot_table(snapshot)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_stage2_prompt(
    snapshot: Mapping[str, Any], stage1: Mapping[str, Any]
) -> list[dict[str, str]]:
    system = (
        "你是价格行为决策引擎。输出 JSON：decision, decision_trace, future_trend, "
        "next_cycle_prediction, next_bar_prediction。不要建议真实下单。"
    )
    user = (
        "阶段一诊断：\n" + json.dumps(stage1, ensure_ascii=False) + "\n"
        "请结合当前趋势、当前周期、下一个周期、支撑阻力、K线结构，生成保守决策路径、"
        "决策树、未来走势预期与下根K线预期。\n"
        "K线（旧到新）：\n" + _snapshot_table(snapshot)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_followup_prompt(
    record: Mapping[str, Any], question: str
) -> list[dict[str, str]]:
    system = "你是量化研究追问助手。基于既有分析回答，不新增交易指令，不给出投资建议。"
    user = (
        "已有分析：\n" + json.dumps(record, ensure_ascii=False) + "\n用户追问：\n" + question
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    raw = match.group(0)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _chat_headers(provider: AIProviderSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    return headers


def call_chat_completion(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
    *,
    stream: bool = False,
) -> str:
    if not provider.api_key:
        raise ValueError("未配置 API Key")
    payload = {
        "model": provider.model,
        "messages": messages,
        "stream": stream,
    }
    if provider.thinking:
        payload["reasoning_effort"] = provider.reasoning_effort
    response = httpx.post(
        f"{provider.base_url}/chat/completions",
        headers=_chat_headers(provider),
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("模型响应缺少 choices")
    return str(choices[0].get("message", {}).get("content") or "")


def stream_chat_completion(provider: AIProviderSettings, messages: list[dict[str, str]]):
    if not provider.api_key:
        raise ValueError("未配置 API Key")
    payload = {
        "model": provider.model,
        "messages": messages,
        "stream": True,
    }
    if provider.thinking:
        payload["reasoning_effort"] = provider.reasoning_effort
    with httpx.stream(
        "POST",
        f"{provider.base_url}/chat/completions",
        headers=_chat_headers(provider),
        json=payload,
        timeout=60.0,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield str(content)


def build_decision_tree_layout(stage1: Mapping[str, Any], stage2: Mapping[str, Any]) -> dict[str, Any]:
    decision = stage2.get("decision") if isinstance(stage2.get("decision"), dict) else {}
    action = decision.get("action") or decision.get("order_type") or "WAIT"
    nodes = [
        {"id": "n1", "label": "数据快照", "question": "快照是否足够？", "answer": "是", "x": 0, "y": 2},
        {"id": "n2", "label": "市场诊断", "question": "趋势是否清晰？", "answer": str(stage1.get("current_trend", {}).get("direction", stage1.get("current_trend", "unknown"))), "x": 1, "y": 1},
        {"id": "n3", "label": "支撑阻力", "question": "是否存在关键区？", "answer": "是", "x": 1, "y": 3},
        {"id": "n4", "label": "周期判断", "question": "当前/下一周期？", "answer": str(stage1.get("current_cycle", "")), "x": 2, "y": 2},
        {"id": "n5", "label": "交易决策", "question": "是否形成方案？", "answer": str(action), "x": 3, "y": 2},
        {"id": "n6", "label": "未来走势", "question": "预期是否有效？", "answer": str(stage2.get("future_trend", {}).get("label", "观察")), "x": 4, "y": 2},
    ]
    edges = [
        {"from": "n1", "to": "n2"},
        {"from": "n1", "to": "n3"},
        {"from": "n2", "to": "n4"},
        {"from": "n3", "to": "n4"},
        {"from": "n4", "to": "n5"},
        {"from": "n5", "to": "n6"},
    ]
    return {"nodes": nodes, "edges": edges}


def run_two_stage(
    snapshot: Mapping[str, Any], settings: AISettings
) -> dict[str, Any]:
    stage1_messages = build_stage1_prompt(snapshot)
    stage2_messages = build_stage2_prompt(snapshot, {})
    record: dict[str, Any] = {
        "id": f"ai-{int(datetime.now(UTC).timestamp() * 1000)}",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "ok",
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "snapshot": snapshot,
        "stage1_messages": stage1_messages,
        "stage2_messages": stage2_messages,
        "raw_prompt": {
            "stage1": stage1_messages,
            "stage2": stage2_messages,
        },
        "stage1_response": "",
        "stage2_response": "",
        "exception": None,
        "usage_total": {},
    }
    if not settings.provider.api_key:
        local_pa = analyze_price_action(
            snapshot["candles"],
            symbol=snapshot["symbol"],
            timeframe=snapshot["timeframe"],
            lookback=len(snapshot["candles"]),
            stance=settings.decision_stance,
        )
        diagnosis = {
            "current_trend": local_pa.get("market_context", {}),
            "current_cycle": local_pa.get("market_context", {}).get("cycle_position", "unknown"),
            "next_cycle": local_pa.get("market_context", {}).get("cycle_position", "unknown"),
            "supports": snapshot.get("support_resistance", {}).get("supports_only", []),
            "resistances": snapshot.get("support_resistance", {}).get("resistances", []),
            "diagnosis_summary": "本地确定性研究模式：未调用外部模型。",
            "key_factors": local_pa.get("features", {}).get("patterns", []),
            "confidence": local_pa.get("decision", {}).get("confidence", 0),
        }
        decision = {
            "action": local_pa.get("decision", {}).get("action", "WAIT"),
            "confidence": local_pa.get("decision", {}).get("confidence", 0),
            "entry": local_pa.get("decision", {}).get("entry"),
            "stop": local_pa.get("decision", {}).get("stop"),
            "target": local_pa.get("decision", {}).get("target"),
            "rr": local_pa.get("decision", {}).get("rr"),
            "reasoning": local_pa.get("decision", {}).get("reasoning", ""),
        }
        record["stage1_diagnosis"] = diagnosis
        record["stage2_decision"] = {
            "decision": decision,
            "future_trend": {"label": "本地研究模式未生成外部预测", "confidence": 0},
            "next_cycle_prediction": {"cycle": diagnosis.get("next_cycle", "unknown")},
            "next_bar_prediction": {"direction": "neutral", "confidence": 0},
        }
        record["decision_tree_layout"] = build_decision_tree_layout(diagnosis, record["stage2_decision"])
        return record

    stage1_text = call_chat_completion(settings.provider, stage1_messages)
    stage1_json = extract_json_object(stage1_text) or {}
    stage2_messages = build_stage2_prompt(snapshot, stage1_json)
    record["stage2_messages"] = stage2_messages
    record["raw_prompt"]["stage2"] = stage2_messages
    stage2_text = call_chat_completion(settings.provider, stage2_messages)
    stage2_json = extract_json_object(stage2_text) or {}
    record.update(
        {
            "stage1_response": stage1_text,
            "stage1_diagnosis": stage1_json,
            "stage2_response": stage2_text,
            "stage2_decision": stage2_json,
            "decision_tree_layout": build_decision_tree_layout(stage1_json, stage2_json),
        }
    )
    return record

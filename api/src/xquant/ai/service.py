from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

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
    proxy_url: str = ""
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class AISettings:
    provider: AIProviderSettings
    analysis_bar_count: int = 120
    decision_stance: str = "balanced"
    enable_next_bar_prediction: bool = True
    keep_analysis: bool = False
    incremental_max_new_bars: int = 10


@dataclass(frozen=True)
class AnalysisSnapshot:
    dataset_id: str
    symbol: str
    timeframe: str
    bars: Sequence[Mapping[str, Any]]
    lookback: int = 120
    decision_stance: str = "balanced"
    enable_next_bar_prediction: bool = True


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_ai_settings(payload: Mapping[str, Any] | None = None) -> AISettings:
    data = dict(payload or {})
    analysis = dict(data.get("analysis") or {}) if isinstance(data.get("analysis"), Mapping) else {}
    provider_data = dict(data.get("provider") or {})
    proxy_url = str(provider_data.get("proxy_url") or "").strip()
    if proxy_url and not re.match(r"^(https?|socks[45]h?)://", proxy_url, re.IGNORECASE):
        raise ValueError("代理地址必须使用 http、https、socks4 或 socks5")
    try:
        timeout_seconds = float(provider_data.get("timeout_seconds") or 60.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("模型请求超时必须是数字") from exc
    if not 1.0 <= timeout_seconds <= 600.0:
        raise ValueError("模型请求超时必须在 1 到 600 秒之间")
    provider = AIProviderSettings(
        model=str(provider_data.get("model") or "deepseek-chat"),
        base_url=str(provider_data.get("base_url") or "https://api.deepseek.com/v1").rstrip("/"),
        api_key=str(provider_data.get("api_key") or provider_data.get("api_key_env") or ""),
        thinking=bool(provider_data.get("thinking") or False),
        reasoning_effort=str(provider_data.get("reasoning_effort") or "medium"),
        context_window=_bounded_int(
            provider_data.get("context_window"),
            default=128000,
            minimum=1000,
            maximum=10_000_000,
        ),
        proxy_url=proxy_url,
        timeout_seconds=timeout_seconds,
    )
    return AISettings(
        provider=provider,
        analysis_bar_count=_bounded_int(
            data.get("analysis_bar_count", analysis.get("analysis_bar_count")),
            default=120,
            minimum=60,
            maximum=1000,
        ),
        decision_stance=str(
            data.get("decision_stance") or analysis.get("decision_stance") or "balanced"
        ),
        enable_next_bar_prediction=bool(
            data.get("enable_next_bar_prediction", analysis.get("enable_next_bar_prediction", True))
        ),
        keep_analysis=bool(data.get("keep_analysis", analysis.get("keep_analysis", False))),
        incremental_max_new_bars=_bounded_int(
            data.get("incremental_max_new_bars", analysis.get("incremental_max_new_bars")),
            default=10,
            minimum=0,
            maximum=500,
        ),
    )


def mask_provider(provider: Mapping[str, Any]) -> dict[str, Any]:
    api_key = str(provider.get("api_key") or "")
    proxy_url = str(provider.get("proxy_url") or "")
    return {
        "model": provider.get("model", ""),
        "base_url": provider.get("base_url", ""),
        "api_key": "***" if api_key else "",
        "thinking": bool(provider.get("thinking")),
        "reasoning_effort": provider.get("reasoning_effort", ""),
        "context_window": provider.get("context_window", 0),
        "proxy_url": _mask_proxy(proxy_url),
        "timeout_seconds": provider.get("timeout_seconds", 60),
    }


def _mask_proxy(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"://([^/@:]+)(?::[^/@]*)?@", r"://***@", value)


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
    levels = sr.get("levels") or []
    return {
        "dataset_id": dataset_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": pa["current_price"],
        "trend": pa["market_context"],
        "support_resistance": {
            "supports": [level for level in levels if level.get("zone_type") == "support"],
            "resistances": [level for level in levels if level.get("zone_type") == "resistance"],
            "supports_only": [level for level in levels if level.get("zone_type") == "support"],
        },
        "features": pa.get("features", {}),
        "decision": pa.get("decision", {}),
        "candles": list(selected),
        "analysis_bar_count": len(selected),
        "snapshot_meta": {
            "first_session": selected[0].get("session_id"),
            "last_session": selected[-1].get("session_id"),
            "closed_only": True,
        },
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
        "你是严谨的价格行为量化研究助手。只基于给定的已收盘K线做市场诊断，"
        "不给出真实投资建议，不执行交易。必须只输出一个 JSON 对象，至少包含："
        "current_trend(direction/strength/cycle_position/details), current_cycle, next_cycle, "
        "supports, resistances, diagnosis_summary, key_factors, confidence, market_phase, "
        "detected_patterns, gate_result, gate_trace。gate_trace 每项含 label、question、answer、status。"
    )
    levels = snapshot.get("support_resistance", {})
    supports = ", ".join(str(level.get("center")) for level in levels.get("supports_only", []))
    resistances = ", ".join(str(level.get("center")) for level in levels.get("resistances", []))
    trend = snapshot.get("trend", {})
    user = (
        f"品种 {snapshot.get('symbol')}，周期 {snapshot.get('timeframe')}。\n"
        f"本地趋势背景：{json.dumps(trend, ensure_ascii=False)}\n"
        f"当前趋势：{trend.get('direction', '未知')}\n"
        f"当前周期位置：{trend.get('cycle_position', '未知')}\n"
        f"候选支撑：{supports or '无'}\n候选阻力：{resistances or '无'}\n"
        "请先识别趋势、周期、结构、关键价位、确认信号与失效条件。\n"
        "K线（旧到新）：\n" + _snapshot_table(snapshot)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_stage2_prompt(
    snapshot: Mapping[str, Any],
    stage1: Mapping[str, Any],
) -> list[dict[str, str]]:
    system = (
        "你是保守的价格行为决策引擎，只做研究模拟。必须只输出一个 JSON 对象，至少包含："
        "decision、diagnosis_summary、decision_trace、terminal、future_trend、"
        "next_cycle_prediction、next_bar_prediction。decision 内包含 action、confidence、"
        "entry、stop、target、rr、reasoning、invalidation、watch_points、risk_flags。"
        "decision_trace 每项包含 phase、label、question、answer、status、reasoning。"
        "未来预测只作旁注，不能改变 decision。"
        "next_bar_prediction 包含 direction、probabilities、confidence、reasoning。"
    )
    user = (
        "阶段一诊断：\n"
        + json.dumps(stage1, ensure_ascii=False)
        + "\n请结合当前趋势、周期、支撑阻力、K线结构与失效条件，"
        "生成决策路径、未来走势预期、下一周期预期和下根K线预期。"
        "若没有充分优势，action 使用 WAIT。\nK线（旧到新）：\n"
        + _snapshot_table(snapshot)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_followup_prompt(record: Mapping[str, Any], question: str) -> list[dict[str, str]]:
    system = "你是量化研究追问助手。基于既有分析回答，不新增真实交易指令，不给出投资建议。"
    user = "已有分析：\n" + json.dumps(record, ensure_ascii=False) + "\n用户追问：\n" + question
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidates = [text.strip()]
    candidates.extend(
        re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    )
    for candidate in candidates:
        for start in (match.start() for match in re.finditer(r"\{", candidate)):
            for end in range(len(candidate), start, -1):
                if candidate[end - 1] != "}":
                    continue
                try:
                    value = json.loads(candidate[start:end])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    return None


def _chat_headers(provider: AIProviderSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    return headers


def _client(provider: AIProviderSettings) -> httpx.Client:
    return httpx.Client(
        timeout=provider.timeout_seconds,
        proxy=provider.proxy_url or None,
        follow_redirects=True,
    )


def _request_payload(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
    *,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "stream": stream,
    }
    if provider.thinking:
        payload["reasoning_effort"] = provider.reasoning_effort
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _stream_payload_variants(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Return progressively smaller payloads for compatible stream endpoints."""

    base = _request_payload(provider, messages, stream=True)
    variants = [base]
    without_usage = dict(base)
    without_usage.pop("stream_options", None)
    if without_usage != variants[-1]:
        variants.append(without_usage)
    without_reasoning = dict(without_usage)
    without_reasoning.pop("reasoning_effort", None)
    if without_reasoning != variants[-1]:
        variants.append(without_reasoning)
    return variants


def _upstream_error_detail(response: httpx.Response) -> str:
    try:
        response.read()
    except httpx.HTTPError:
        pass
    payload: Any
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return response.text.strip()[:1000]
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            detail = error.get("message") or error.get("detail")
            if detail:
                return str(detail)[:1000]
        detail = payload.get("detail") or payload.get("message")
        if detail:
            return str(detail)[:1000]
    return str(payload)[:1000]


def _upstream_error_message(response: httpx.Response) -> str:
    detail = _upstream_error_detail(response)
    suffix = f"：{detail}" if detail else ""
    return f"模型服务返回 HTTP {response.status_code}{suffix}"


def _reply_from_response(data: Mapping[str, Any], latency_ms: float) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("模型响应缺少 choices")
    message = choices[0].get("message") or {}
    usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
    return {
        "id": data.get("id") or "",
        "model": data.get("model") or "",
        "content": str(message.get("content") or ""),
        "reasoning_content": str(message.get("reasoning_content") or message.get("reasoning") or ""),
        "usage": dict(usage),
        "latency_ms": round(latency_ms, 2),
        "finish_reason": choices[0].get("finish_reason"),
    }


def _reply_from_sse_text(text: str, latency_ms: float) -> dict[str, Any]:
    """Parse SSE chunks that a compatible gateway returns even for stream=false."""

    request_id = ""
    model = ""
    usage: dict[str, Any] = {}
    finish_reason: str | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    choices_seen = False

    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, Mapping):
            continue
        request_id = str(chunk.get("id") or request_id)
        model = str(chunk.get("model") or model)
        if isinstance(chunk.get("usage"), Mapping):
            usage = dict(chunk["usage"])
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choices_seen = True
        choice = choices[0]
        finish_reason = choice.get("finish_reason") or finish_reason
        message = choice.get("message") or choice.get("delta") or {}
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            reasoning_parts.append(str(reasoning))
        content = message.get("content")
        if content:
            content_parts.append(str(content))

    if not choices_seen:
        raise ValueError("模型响应不是有效的 JSON 或 SSE")
    return {
        "id": request_id,
        "model": model,
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts),
        "usage": usage,
        "latency_ms": round(latency_ms, 2),
        "finish_reason": finish_reason,
    }


def _reply_from_http_response(
    response: httpx.Response,
    latency_ms: float,
) -> dict[str, Any]:
    text = response.text
    if not text.strip():
        raise ValueError(f"模型响应为空（HTTP {response.status_code}）")
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _reply_from_sse_text(text, latency_ms)
    if not isinstance(data, Mapping):
        data = {}
    return _reply_from_response(data, latency_ms)


def _chat_completion_urls(provider: AIProviderSettings) -> list[str]:
    base_url = provider.base_url.rstrip("/")
    urls = [f"{base_url}/chat/completions"]
    path = urlsplit(base_url).path.strip("/")
    last_segment = path.rsplit("/", 1)[-1] if path else ""
    if not re.fullmatch(r"v\d+", last_segment, re.IGNORECASE):
        urls.append(f"{base_url}/v1/chat/completions")
    return list(dict.fromkeys(urls))


def _post_chat_completion(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    if not provider.api_key:
        raise ValueError("未配置 API Key")
    started = time.perf_counter()
    urls = _chat_completion_urls(provider)
    last_error: Exception | None = None
    with _client(provider) as client:
        for index, url in enumerate(urls):
            response = client.post(
                url,
                headers=_chat_headers(provider),
                json=_request_payload(provider, messages, stream=False),
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = ValueError(_upstream_error_message(response))
                if response.status_code not in {404, 405} and index < len(urls) - 1:
                    continue
                raise last_error from exc
            try:
                reply = _reply_from_http_response(response, (time.perf_counter() - started) * 1000)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if index < len(urls) - 1:
                    continue
                raise
            if not str(reply.get("content") or "").strip() and index < len(urls) - 1:
                continue
            return reply
    if last_error:
        raise last_error
    raise ValueError("模型未返回内容")


def call_chat_completion(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
    *,
    stream: bool = False,
) -> str:
    if stream:
        return "".join(stream_chat_completion(provider, messages))
    return str(_post_chat_completion(provider, messages)["content"])


def stream_chat_completion(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
) -> Iterator[str]:
    for event in stream_chat_completion_events(provider, messages):
        if event.get("type") == "content" and event.get("text"):
            yield str(event["text"])


def stream_chat_completion_events(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
) -> Iterator[dict[str, Any]]:
    if not provider.api_key:
        raise ValueError("未配置 API Key")
    started = time.perf_counter()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    request_id = ""
    model = ""
    usage: dict[str, Any] = {}
    finish_reason: str | None = None
    payload_variants = _stream_payload_variants(provider, messages)
    urls = _chat_completion_urls(provider)
    with _client(provider) as client:
        for index, request_payload in enumerate(payload_variants):
            for url_index, url in enumerate(urls):
                with client.stream(
                    "POST",
                    url,
                    headers=_chat_headers(provider),
                    json=request_payload,
                ) as response:
                    if response.status_code in {400, 422} and index < len(payload_variants) - 1:
                        response.read()
                        break
                    if response.status_code in {404, 405} and url_index < len(urls) - 1:
                        response.read()
                        continue
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise ValueError(_upstream_error_message(response)) from exc
                    for line in response.iter_lines():
                        if time.perf_counter() - started > provider.timeout_seconds:
                            raise httpx.ReadTimeout(
                                f"模型流式响应超过 {provider.timeout_seconds:g} 秒未完成"
                            )
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        request_id = chunk.get("id") or request_id
                        model = chunk.get("model") or model
                        if isinstance(chunk.get("usage"), Mapping):
                            usage = dict(chunk["usage"])
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        finish_reason = choice.get("finish_reason") or finish_reason
                        message = choice.get("message") or {}
                        delta = choice.get("delta") or {}
                        reasoning = (
                            delta.get("reasoning_content")
                            or delta.get("reasoning")
                            or message.get("reasoning_content")
                            or message.get("reasoning")
                        )
                        if reasoning:
                            reasoning_parts.append(str(reasoning))
                            yield {"type": "reasoning", "text": str(reasoning)}
                        content = delta.get("content") or message.get("content")
                        if content:
                            content_parts.append(str(content))
                            yield {"type": "content", "text": str(content)}
                if content_parts or reasoning_parts:
                    break
            if content_parts or reasoning_parts:
                break
    yield {
        "type": "usage",
        "reply": {
            "id": request_id,
            "model": model,
            "content": "".join(content_parts),
            "reasoning_content": "".join(reasoning_parts),
            "usage": usage,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "finish_reason": finish_reason,
        },
    }


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


def normalize_diagnosis(raw: Mapping[str, Any] | None, fallback: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(raw or {})
    trend = data.get("current_trend")
    if isinstance(trend, str):
        trend = {"direction": trend}
    if not isinstance(trend, Mapping):
        trend = fallback.get("trend") if isinstance(fallback.get("trend"), Mapping) else {}
    trend_data = dict(trend)
    direction = _first(data, "direction", default=trend_data.get("direction", "neutral"))
    sr = fallback.get("support_resistance")
    if not isinstance(sr, Mapping):
        sr = {}
    return {
        "current_trend": {
            **trend_data,
            "direction": direction,
            "strength": _first(data, "trend_strength", default=trend_data.get("strength")),
            "cycle_position": _first(
                data,
                "cycle_position",
                "current_cycle",
                default=trend_data.get("cycle_position"),
            ),
        },
        "current_cycle": _first(
            data,
            "current_cycle",
            "cycle_position",
            default=trend_data.get("cycle_position", "unknown"),
        ),
        "next_cycle": _first(data, "next_cycle", "next_cycle_prediction", default="unknown"),
        "supports": _as_list(data.get("supports") or sr.get("supports_only")),
        "resistances": _as_list(data.get("resistances") or sr.get("resistances")),
        "diagnosis_summary": _first(
            data,
            "diagnosis_summary",
            "summary",
            default="未返回诊断摘要。",
        ),
        "key_factors": _as_list(_first(data, "key_factors", "key_signals", default=[])),
        "confidence": _first(data, "confidence", "diagnosis_confidence", default=0),
        "market_phase": _first(data, "market_phase", default="unknown"),
        "detected_patterns": _as_list(_first(data, "detected_patterns", "patterns", default=[])),
        "gate_result": _first(data, "gate_result", default="unknown"),
        "gate_trace": _as_list(data.get("gate_trace")),
        "raw": data,
    }


def normalize_stage2(
    raw: Mapping[str, Any] | None,
    diagnosis: Mapping[str, Any],
) -> dict[str, Any]:
    data = dict(raw or {})
    decision = data.get("decision")
    if not isinstance(decision, Mapping):
        decision = data
    decision_data = dict(decision)
    action = str(
        _first(decision_data, "action", "order_type", "order_direction", default="WAIT")
    ).upper()
    if action in {"BUY", "LONG", "做多"}:
        action = "LONG"
    elif action in {"SELL", "SHORT", "做空"}:
        action = "SHORT"
    elif action in {"HOLD", "NONE", "WAIT", "不下单", "观望"}:
        action = "WAIT"
    normalized_decision = {
        **decision_data,
        "action": action,
        "confidence": _first(
            decision_data,
            "confidence",
            "trade_confidence",
            "diagnosis_confidence",
            default=0,
        ),
        "entry": _first(decision_data, "entry", "entry_price", "entry_reference"),
        "stop": _first(decision_data, "stop", "stop_loss", "stop_loss_price"),
        "target": _first(
            decision_data,
            "target",
            "take_profit",
            "take_profit_price",
            "target_price",
        ),
        "target_2": _first(decision_data, "target_2", "take_profit_price_2"),
        "rr": _first(decision_data, "rr", "risk_reward", "risk_reward_ratio"),
        "reasoning": _first(decision_data, "reasoning", "summary", default=""),
        "invalidation": _first(decision_data, "invalidation", "invalid_condition"),
        "watch_points": _as_list(
            decision_data.get("watch_points") or decision_data.get("key_factors")
        ),
        "risk_flags": _as_list(decision_data.get("risk_flags") or decision_data.get("risks")),
    }
    future = data.get("future_trend")
    if not isinstance(future, Mapping):
        future = {"label": future or "观察", "confidence": 0}
    next_cycle = data.get("next_cycle_prediction")
    if not isinstance(next_cycle, Mapping):
        next_cycle = {"cycle": next_cycle or diagnosis.get("next_cycle", "unknown")}
    next_bar = data.get("next_bar_prediction")
    if next_bar is None:
        next_bar = data.get("next_bar")
    if not isinstance(next_bar, Mapping):
        next_bar = {
            "direction": next_bar or "neutral",
            "probabilities": {},
            "confidence": 0,
            "reasoning": "",
        }
    return {
        "decision": normalized_decision,
        "diagnosis_summary": _first(
            data,
            "diagnosis_summary",
            default=diagnosis.get("diagnosis_summary", ""),
        ),
        "decision_trace": _as_list(data.get("decision_trace")),
        "terminal": data.get("terminal") if isinstance(data.get("terminal"), Mapping) else {},
        "future_trend": dict(future),
        "next_cycle_prediction": dict(next_cycle),
        "next_bar_prediction": dict(next_bar),
        "raw": data,
    }


def _local_diagnosis(snapshot: Mapping[str, Any], local_pa: Mapping[str, Any]) -> dict[str, Any]:
    market = local_pa.get("market_context", {})
    sr = snapshot.get("support_resistance")
    if not isinstance(sr, Mapping):
        sr = {}
    return {
        "current_trend": market,
        "current_cycle": market.get("cycle_position", "unknown"),
        "next_cycle": market.get("cycle_position", "unknown"),
        "supports": sr.get("supports_only", []),
        "resistances": sr.get("resistances", []),
        "diagnosis_summary": "本地确定性研究模式：未调用外部模型。",
        "key_factors": local_pa.get("features", {}).get("patterns", []),
        "confidence": local_pa.get("decision", {}).get("confidence", 0),
        "market_phase": market.get("cycle_position", "unknown"),
        "detected_patterns": local_pa.get("features", {}).get("patterns", []),
        "gate_result": "local",
        "gate_trace": [],
    }


def _local_stage2(local_pa: Mapping[str, Any], diagnosis: Mapping[str, Any]) -> dict[str, Any]:
    decision = dict(local_pa.get("decision", {}))
    decision["reasoning"] = decision.get("reasoning") or "本地确定性规则给出的保守研究结果。"
    return {
        "decision": decision,
        "diagnosis_summary": diagnosis.get("diagnosis_summary", ""),
        "decision_trace": [
            {
                "phase": "gate",
                "label": "数据与结构",
                "question": "是否具备可研究结构？",
                "answer": "是",
                "status": "passed",
                "reasoning": diagnosis.get("diagnosis_summary", ""),
            },
            {
                "phase": "decision",
                "label": "确定性决策",
                "question": "是否满足本地规则？",
                "answer": decision.get("action", "WAIT"),
                "status": "terminal",
                "reasoning": decision.get("reasoning", ""),
            },
        ],
        "terminal": {
            "outcome": decision.get("action", "WAIT"),
            "reasoning": decision.get("reasoning", ""),
        },
        "future_trend": {"label": "本地研究模式未生成外部预测", "confidence": 0},
        "next_cycle_prediction": {"cycle": diagnosis.get("next_cycle", "unknown")},
        "next_bar_prediction": {
            "direction": "neutral",
            "probabilities": {"up": 0, "down": 0, "neutral": 100},
            "confidence": 0,
            "reasoning": "未调用外部模型。",
        },
    }


def build_decision_tree_layout(
    stage1: Mapping[str, Any],
    stage2: Mapping[str, Any],
) -> dict[str, Any]:
    decision = stage2.get("decision") if isinstance(stage2.get("decision"), Mapping) else {}
    trace = _as_list(stage2.get("decision_trace"))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def add_node(
        *,
        node_id: str,
        label: str,
        question: str,
        answer: str,
        x: int,
        y: int,
        phase: str,
        status: str = "",
        reasoning: str = "",
    ) -> None:
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "question": question,
                "answer": answer,
                "x": x,
                "y": y,
                "phase": phase,
                "status": status,
                "reasoning": reasoning,
            }
        )

    add_node(
        node_id="n1",
        label="数据快照",
        question="是否满足分析条件？",
        answer="足够",
        x=0,
        y=2,
        phase="gate",
        status="passed",
    )
    if trace:
        previous = "n1"
        for index, item in enumerate(trace):
            node_id = f"trace-{index + 1}"
            entry = dict(item) if isinstance(item, Mapping) else {"answer": str(item)}
            add_node(
                node_id=node_id,
                label=str(_first(entry, "label", "node", "name", default=f"步骤 {index + 1}")),
                question=str(_first(entry, "question", default="是否通过？")),
                answer=str(_first(entry, "answer", "result", default="-")),
                x=index + 1,
                y=2 if index % 2 == 0 else 1,
                phase=str(_first(entry, "phase", default="decision")),
                status=str(_first(entry, "status", default="")),
                reasoning=str(_first(entry, "reasoning", "detail", default="")),
            )
            edges.append({"from": previous, "to": node_id})
            previous = node_id
    else:
        trend = stage1.get("current_trend")
        direction = trend.get("direction") if isinstance(trend, Mapping) else trend
        add_node(
            node_id="n2",
            label="市场诊断",
            question="趋势是否清晰？",
            answer=str(direction or "观察"),
            x=1,
            y=1,
            phase="diagnosis",
        )
        add_node(
            node_id="n3",
            label="结构确认",
            question="关键位是否有效？",
            answer="待确认",
            x=1,
            y=3,
            phase="gate",
        )
        add_node(
            node_id="n4",
            label="周期判断",
            question="当前/下一周期？",
            answer=str(
                _first(
                    stage1,
                    "current_cycle",
                    default=stage1.get("next_cycle", "unknown"),
                )
            ),
            x=2,
            y=2,
            phase="diagnosis",
        )
        add_node(
            node_id="n5",
            label="交易决策",
            question="是否形成方案？",
            answer=str(decision.get("action") or decision.get("order_type") or "WAIT"),
            x=3,
            y=2,
            phase="decision",
        )
        future = stage2.get("future_trend")
        future_label = future.get("label") if isinstance(future, Mapping) else future
        add_node(
            node_id="n6",
            label="未来走势",
            question="预期是否有效？",
            answer=str(future_label or "观察"),
            x=4,
            y=2,
            phase="terminal",
            status="terminal",
        )
        edges.extend(
            [
                {"from": "n1", "to": "n2"},
                {"from": "n1", "to": "n3"},
                {"from": "n2", "to": "n4"},
                {"from": "n3", "to": "n4"},
                {"from": "n4", "to": "n5"},
                {"from": "n5", "to": "n6"},
            ]
        )
        return {"nodes": nodes, "edges": edges}
    previous = nodes[-1]["id"]
    terminal = stage2.get("terminal")
    outcome = terminal.get("outcome") if isinstance(terminal, Mapping) else decision.get("action")
    add_node(
        node_id="terminal",
        label="最终结果",
        question="本轮结论？",
        answer=str(outcome or decision.get("action") or "WAIT"),
        x=max(node["x"] for node in nodes) + 1,
        y=2,
        phase="terminal",
        status="terminal",
        reasoning=str(terminal.get("reasoning", "")) if isinstance(terminal, Mapping) else "",
    )
    edges.append({"from": previous, "to": "terminal"})
    return {"nodes": nodes, "edges": edges}


def _usage_total(*replies: Mapping[str, Any]) -> dict[str, int]:
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
    }
    for reply in replies:
        usage = reply.get("usage") if isinstance(reply.get("usage"), Mapping) else {}
        for key in totals:
            totals[key] += int(usage.get(key) or 0)
    return totals


def _debug_payload(
    settings: AISettings,
    *,
    stage1_reply: Mapping[str, Any] | None = None,
    stage2_reply: Mapping[str, Any] | None = None,
    attempts: int = 0,
    validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider": mask_provider(
            {
                "model": settings.provider.model,
                "base_url": settings.provider.base_url,
                "api_key": settings.provider.api_key,
                "thinking": settings.provider.thinking,
                "reasoning_effort": settings.provider.reasoning_effort,
                "context_window": settings.provider.context_window,
                "proxy_url": settings.provider.proxy_url,
                "timeout_seconds": settings.provider.timeout_seconds,
            }
        ),
        "attempts": attempts,
        "stage1": {
            "request_id": (stage1_reply or {}).get("id", ""),
            "latency_ms": (stage1_reply or {}).get("latency_ms", 0),
            "finish_reason": (stage1_reply or {}).get("finish_reason"),
            "usage": (stage1_reply or {}).get("usage", {}),
        },
        "stage2": {
            "request_id": (stage2_reply or {}).get("id", ""),
            "latency_ms": (stage2_reply or {}).get("latency_ms", 0),
            "finish_reason": (stage2_reply or {}).get("finish_reason"),
            "usage": (stage2_reply or {}).get("usage", {}),
        },
        "validation": dict(validation or {}),
    }


def _record_base(
    snapshot: Mapping[str, Any],
    stage1_messages: list[dict[str, str]],
    stage2_messages: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": f"ai-{int(datetime.now(UTC).timestamp() * 1000)}",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "ok",
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "snapshot": snapshot,
        "stage1_messages": stage1_messages,
        "stage2_messages": stage2_messages,
        "raw_prompt": {"stage1": stage1_messages, "stage2": stage2_messages},
        "stage1_response": "",
        "stage2_response": "",
        "stage1_response_text": "",
        "stage2_response_text": "",
        "stage1_diagnosis": {},
        "stage2_decision": {},
        "decision_trace": [],
        "future_trend": {},
        "next_cycle_prediction": {},
        "next_bar_prediction": {},
        "decision_tree_layout": {},
        "usage_total": {},
        "debug": {},
        "exception": None,
    }


def _finalize_record(
    record: dict[str, Any],
    *,
    diagnosis: Mapping[str, Any],
    stage2: Mapping[str, Any],
    stage1_reply: Mapping[str, Any],
    stage2_reply: Mapping[str, Any],
    settings: AISettings,
    attempts: int,
) -> dict[str, Any]:
    record.update(
        {
            "status": "ok",
            "stage1_diagnosis": dict(diagnosis),
            "stage2_decision": dict(stage2),
            "decision_trace": list(stage2.get("decision_trace") or []),
            "future_trend": dict(stage2.get("future_trend") or {}),
            "next_cycle_prediction": dict(stage2.get("next_cycle_prediction") or {}),
            "next_bar_prediction": dict(stage2.get("next_bar_prediction") or {}),
            "decision_tree_layout": build_decision_tree_layout(diagnosis, stage2),
            "stage1_response": dict(stage1_reply),
            "stage2_response": dict(stage2_reply),
            "stage1_response_text": str(stage1_reply.get("content") or ""),
            "stage2_response_text": str(stage2_reply.get("content") or ""),
            "usage_total": _usage_total(stage1_reply, stage2_reply),
            "debug": _debug_payload(
                settings,
                stage1_reply=stage1_reply,
                stage2_reply=stage2_reply,
                attempts=attempts,
            ),
            "exception": None,
        }
    )
    return record


def run_two_stage(snapshot: Mapping[str, Any], settings: AISettings) -> dict[str, Any]:
    stage1_messages = build_stage1_prompt(snapshot)
    initial_stage2_messages = build_stage2_prompt(snapshot, {})
    record = _record_base(snapshot, stage1_messages, initial_stage2_messages)
    started = time.perf_counter()
    local_pa = analyze_price_action(
        snapshot["candles"],
        symbol=snapshot["symbol"],
        timeframe=snapshot["timeframe"],
        lookback=len(snapshot["candles"]),
        stance=settings.decision_stance,
    )
    if not settings.provider.api_key:
        diagnosis = _local_diagnosis(snapshot, local_pa)
        stage2 = _local_stage2(local_pa, diagnosis)
        placeholder_reply = {
            "content": json.dumps(stage2, ensure_ascii=False),
            "reasoning_content": "",
            "usage": {},
            "latency_ms": 0,
            "id": f"local-{record['id']}",
        }
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return _finalize_record(
            record,
            diagnosis=diagnosis,
            stage2=stage2,
            stage1_reply=placeholder_reply,
            stage2_reply=placeholder_reply,
            settings=settings,
            attempts=0,
        )

    try:
        stage1_reply = _post_chat_completion(settings.provider, stage1_messages)
        stage1_raw = extract_json_object(str(stage1_reply.get("content") or "")) or {}
        diagnosis = normalize_diagnosis(stage1_raw, snapshot)
        stage2_messages = build_stage2_prompt(snapshot, diagnosis)
        record["stage2_messages"] = stage2_messages
        record["raw_prompt"]["stage2"] = stage2_messages
        stage2_reply = _post_chat_completion(settings.provider, stage2_messages)
        stage2_raw = extract_json_object(str(stage2_reply.get("content") or "")) or {}
        stage2 = normalize_stage2(stage2_raw, diagnosis)
        if not settings.enable_next_bar_prediction:
            stage2["next_bar_prediction"] = {
                "direction": "disabled",
                "probabilities": {},
                "confidence": 0,
                "reasoning": "本轮未启用下根K线预期。",
            }
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return _finalize_record(
            record,
            diagnosis=diagnosis,
            stage2=stage2,
            stage1_reply=stage1_reply,
            stage2_reply=stage2_reply,
            settings=settings,
            attempts=1,
        )
    except (
        httpx.HTTPError,
        ImportError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        record["status"] = "error"
        record["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "stage": "stage2" if record.get("stage1_diagnosis") else "stage1",
        }
        record["debug"] = _debug_payload(settings, attempts=1)
        return record


def _record_from_stream(
    snapshot: Mapping[str, Any],
    settings: AISettings,
    record: dict[str, Any],
    stage1_reply: Mapping[str, Any],
    stage2_reply: Mapping[str, Any],
) -> dict[str, Any]:
    stage1_raw = extract_json_object(str(stage1_reply.get("content") or "")) or {}
    diagnosis = normalize_diagnosis(stage1_raw, snapshot)
    stage2_messages = build_stage2_prompt(snapshot, diagnosis)
    stage2_raw = extract_json_object(str(stage2_reply.get("content") or "")) or {}
    stage2 = normalize_stage2(stage2_raw, diagnosis)
    if not settings.enable_next_bar_prediction:
        stage2["next_bar_prediction"] = {
            "direction": "disabled",
            "probabilities": {},
            "confidence": 0,
            "reasoning": "本轮未启用下根K线预期。",
        }
    record["stage2_messages"] = stage2_messages
    record["raw_prompt"]["stage2"] = stage2_messages
    return _finalize_record(
        record,
        diagnosis=diagnosis,
        stage2=stage2,
        stage1_reply=stage1_reply,
        stage2_reply=stage2_reply,
        settings=settings,
        attempts=1,
    )


def _consume_stream_reply(
    provider: AIProviderSettings,
    messages: list[dict[str, str]],
    stage: str,
) -> Iterator[dict[str, Any]]:
    final_reply: dict[str, Any] = {
        "content": "",
        "reasoning_content": "",
        "usage": {},
        "latency_ms": 0,
        "id": "",
    }
    for event in stream_chat_completion_events(provider, messages):
        event_type = event.get("type")
        if event_type == "reasoning":
            yield {"type": f"{stage}_reasoning", "text": event.get("text", "")}
        elif event_type == "content":
            yield {"type": stage, "text": event.get("text", "")}
        elif event_type == "usage":
            final_reply = dict(event.get("reply") or final_reply)
    if not str(final_reply.get("content") or "").strip():
        yield {
            "type": "log",
            "text": f"{'阶段一' if stage == 'stage1' else '阶段二'}流式接口未返回正文，已切换普通请求重试。",
        }
        final_reply = _post_chat_completion(provider, messages)
        if not str(final_reply.get("content") or "").strip():
            raise ValueError(f"模型未返回{'阶段一诊断' if stage == 'stage1' else '阶段二决策'}内容")
        yield {"type": stage, "text": final_reply["content"]}
    yield {"type": "__reply__", "reply": final_reply}


def _mark_stream_error(
    record: dict[str, Any],
    settings: AISettings,
    started: float,
    exc: Exception,
    *,
    stage: str,
) -> dict[str, Any]:
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    record["status"] = "error"
    record["exception"] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "stage": stage,
    }
    record["debug"] = _debug_payload(settings, attempts=1)
    return record


def stream_two_stage(
    snapshot: Mapping[str, Any],
    settings: AISettings,
) -> Iterator[dict[str, Any]]:
    stage1_messages = build_stage1_prompt(snapshot)
    record = _record_base(snapshot, stage1_messages, [])
    started = time.perf_counter()
    yield {
        "type": "snapshot",
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "bar_count": len(snapshot.get("candles", [])),
    }
    if not settings.provider.api_key:
        try:
            yield {"type": "log", "text": "进入本地确定性研究模式。"}
            result = run_two_stage(snapshot, settings)
            yield {"type": "done", "record": result}
        except Exception as exc:  # noqa: BLE001 - SSE must always emit a final event
            result = _mark_stream_error(record, settings, started, exc, stage="local")
            yield {"type": "error", "message": str(exc), "record": result}
            yield {"type": "done", "record": result}
        return

    stage = "stage1"
    try:
        yield {"type": "log", "text": "正在执行阶段一：市场诊断..."}
        stage1_reply: dict[str, Any] = {}
        for event in _consume_stream_reply(settings.provider, stage1_messages, "stage1"):
            if event["type"] == "__reply__":
                stage1_reply = dict(event["reply"])
            else:
                yield event
        stage1_raw = extract_json_object(str(stage1_reply.get("content") or "")) or {}
        diagnosis = normalize_diagnosis(stage1_raw, snapshot)
        record["stage1_diagnosis"] = dict(diagnosis)
        record["stage1_response"] = dict(stage1_reply)
        record["stage1_response_text"] = str(stage1_reply.get("content") or "")
        stage2_messages = build_stage2_prompt(snapshot, diagnosis)
        record["stage2_messages"] = stage2_messages
        record["raw_prompt"]["stage2"] = stage2_messages
        yield {"type": "stage1_diagnosis", "diagnosis": diagnosis}
        yield {"type": "log", "text": "阶段一完成，正在执行阶段二：决策生成..."}

        stage = "stage2"
        stage2_reply: dict[str, Any] = {}
        for event in _consume_stream_reply(settings.provider, stage2_messages, "stage2"):
            if event["type"] == "__reply__":
                stage2_reply = dict(event["reply"])
            else:
                yield event
        record = _record_from_stream(snapshot, settings, record, stage1_reply, stage2_reply)
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        yield {"type": "stage2_decision", "decision": record.get("stage2_decision", {})}
        yield {"type": "debug", "debug": record.get("debug", {})}
        yield {"type": "done", "record": record}
    except Exception as exc:  # noqa: BLE001 - SSE must always emit a final event
        record = _mark_stream_error(record, settings, started, exc, stage=stage)
        yield {"type": "error", "message": str(exc), "record": record}
        yield {"type": "done", "record": record}

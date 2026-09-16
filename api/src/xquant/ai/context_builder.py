from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from .service import AIProviderSettings, AISettings, call_chat_completion, extract_json_object

_DEFAULT_PROFILE = {
    "strategic": "1d",
    "tactical": "1h",
    "confirmation": "30m",
    "execution": "15m",
}
_ROLE_LABELS = {
    "strategic": "战略",
    "tactical": "战术",
    "confirmation": "确认",
    "execution": "执行",
}
_BAR_KEYS = {
    "bar",
    "bars",
    "candle",
    "candles",
    "kline",
    "klines",
    "ohlc",
    "ohlcv",
    "raw_bars",
    "price_bars",
}
_BAR_RECORD_KEYS = {"open", "high", "low", "close"}
_RESULT_FIELDS = (
    "status",
    "source",
    "summary",
    "cycle_explanation",
    "scenarios",
    "risks",
    "trigger_conditions",
    "invalid_conditions",
    "notification_text",
    "generated_at",
    "raw_response",
)

_SYSTEM_PROMPT = (
    "你是结构化量化结果的解释助手。你只能解释输入中的量化分析、多周期融合和决策结果，"
    "不得重新计算趋势、支撑阻力、指标或量化决策，不得改变周期角色映射、状态、评分、"
    "触发条件或失效条件，不得给出真实投资建议、交易指令或收益承诺。"
    "必须只输出一个 JSON 对象，不要输出 Markdown、代码围栏或 JSON 之外的文字。"
)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _is_bar_key(value: Any) -> bool:
    key = _normalized_key(value)
    return (
        key in _BAR_KEYS
        or key.endswith(("_bars", "_candles", "_klines", "_ohlc", "_ohlcv"))
    )


def _looks_like_bar_record(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = {_normalized_key(key) for key in value}
    return len(keys & _BAR_RECORD_KEYS) >= 3


def _looks_like_bar_series(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and any(
        _looks_like_bar_record(item) for item in value
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if not _is_bar_key(key)
            and not _looks_like_bar_record(item)
            and not _looks_like_bar_series(item)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _timeframe_name(value: Any, default: str) -> str:
    if isinstance(value, Mapping):
        value = _first_present(
            value,
            "timeframe",
            "period",
            "interval",
            "name",
        )
    text = str(value or "").strip().lower()
    return text or default


def _profile_from_multi_timeframe(multi_timeframe: Mapping[str, Any]) -> dict[str, str]:
    metadata = _mapping(multi_timeframe.get("metadata"))
    profile = _mapping(metadata.get("profile"))
    if not profile:
        profile = _mapping(metadata.get("multi_timeframe_profile"))
    if not profile:
        profile = _mapping(multi_timeframe.get("profile"))
    return {
        role: _timeframe_name(profile.get(role), default)
        for role, default in _DEFAULT_PROFILE.items()
    }


def _timeframe_index(
    timeframes: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for raw_timeframe, raw_analysis in timeframes.items():
        timeframe = str(raw_timeframe or "").strip().lower()
        if not timeframe or not isinstance(raw_analysis, Mapping) or timeframe in result:
            continue
        result[timeframe] = _mapping(_json_safe(raw_analysis))
    return result


def _first_present(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def _role_analysis(
    timeframe: str,
    timeframe_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source = timeframe_index.get(timeframe)
    if source is None:
        return {"timeframe": timeframe, "available": False}
    result = {"timeframe": timeframe, "available": True}
    result.update(_json_safe(source))
    result["timeframe"] = timeframe
    result["available"] = True
    return result


def _volume_price_for_role(
    timeframe: str,
    timeframe_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source = timeframe_index.get(timeframe)
    if source is None:
        return {
            "timeframe": timeframe,
            "available": False,
            "volume_state": None,
            "price_structure": None,
        }
    nested = _mapping(source.get("volume_price"))
    features = _mapping(source.get("features"))
    volume_state = _first_present(
        source,
        "volume_state",
        "volume_regime",
    )
    if volume_state is None:
        volume_state = _first_present(
            nested,
            "volume_state",
            "state",
            "regime",
        )
    if volume_state is None:
        volume_state = _first_present(features, "volume_state", "volume_regime")
    price_structure = _first_present(
        source,
        "price_structure",
        "structure",
    )
    if price_structure is None:
        price_structure = _first_present(
            nested,
            "price_structure",
            "structure",
        )
    if price_structure is None:
        price_structure = _first_present(features, "price_structure", "structure")
    return {
        "timeframe": timeframe,
        "available": True,
        "volume_state": _json_safe(volume_state),
        "price_structure": _json_safe(price_structure),
    }


def _generated_at(value: str | None) -> str:
    text = str(value or "").strip()
    return text or datetime.now(UTC).isoformat()


def build_quant_analysis_context(
    *,
    symbol: str,
    name: str | None,
    quote: Mapping[str, Any] | None,
    timeframes: Mapping[str, Mapping[str, Any]],
    multi_timeframe: Mapping[str, Any],
    decision: Mapping[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    profile = _profile_from_multi_timeframe(multi_timeframe)
    timeframe_index = _timeframe_index(timeframes)
    roles = {
        role: _role_analysis(timeframe, timeframe_index)
        for role, timeframe in profile.items()
    }
    volume_price = {
        role: _volume_price_for_role(timeframe, timeframe_index)
        for role, timeframe in profile.items()
    }
    return {
        "symbol": str(symbol or ""),
        "name": name,
        "quote": _json_safe(dict(quote or {})),
        **roles,
        "volume_price": volume_price,
        "multi_timeframe": _json_safe(dict(multi_timeframe or {})),
        "quant_decision": _json_safe(dict(decision or {})),
        "generated_at": _generated_at(generated_at),
    }


def build_quant_analysis_prompt(context: Mapping[str, Any]) -> list[dict[str, str]]:
    user = (
        "请只输出 JSON，且只输出一个 JSON 对象。字段固定为 summary、cycle_explanation、scenarios、"
        "risks、trigger_conditions、invalid_conditions、notification_text。"
        "summary 和 cycle_explanation 使用简洁字符串；scenarios 使用对象数组；"
        "risks、trigger_conditions、invalid_conditions 使用字符串数组；"
        "notification_text 使用简洁字符串。只解释下面 context 中已经计算好的结果。\n"
        "结构化量化上下文：\n"
        + json.dumps(
            _json_safe(dict(context or {})),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _text_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    safe = _json_safe(value)
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _list_field(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return _json_safe(value)
    if isinstance(value, tuple):
        return _json_safe(list(value))
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [_json_safe(value)]


def _condition_list(value: Any) -> list[str]:
    return [str(item) for item in _list_field(value) if item is not None]


def _result_base(
    *,
    status: str,
    source: str,
    generated_at: str,
    raw_response: str,
) -> dict[str, Any]:
    result = {
        "status": status,
        "source": source,
        "summary": "",
        "cycle_explanation": "",
        "scenarios": [],
        "risks": [],
        "trigger_conditions": [],
        "invalid_conditions": [],
        "notification_text": "",
        "generated_at": generated_at,
        "raw_response": raw_response,
    }
    return {field: result[field] for field in _RESULT_FIELDS}


def _normalize_model_result(
    payload: Mapping[str, Any],
    *,
    generated_at: str,
    raw_response: str,
) -> dict[str, Any]:
    result = _result_base(
        status="ok",
        source="model",
        generated_at=generated_at,
        raw_response=raw_response,
    )
    result.update(
        {
            "summary": _text_field(payload.get("summary")),
            "cycle_explanation": _text_field(payload.get("cycle_explanation")),
            "scenarios": _list_field(payload.get("scenarios")),
            "risks": _condition_list(payload.get("risks")),
            "trigger_conditions": _condition_list(payload.get("trigger_conditions")),
            "invalid_conditions": _condition_list(payload.get("invalid_conditions")),
            "notification_text": _text_field(payload.get("notification_text")),
        }
    )
    return result


def _local_cycle_explanation(context: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for role, label in _ROLE_LABELS.items():
        role_data = _mapping(context.get(role))
        timeframe = str(role_data.get("timeframe") or "")
        if not role_data.get("available"):
            parts.append(f"{label}周期 {timeframe or '未映射'}：缺失")
            continue
        state = _first_present(
            role_data,
            "trend",
            "state",
            "phase",
            "summary_state",
        )
        parts.append(f"{label}周期 {timeframe}：{state if state is not None else '未提供'}")
    return "；".join(parts) + "。"


def _local_summary(context: Mapping[str, Any]) -> str:
    symbol = str(context.get("symbol") or "")
    name = str(context.get("name") or "").strip()
    multi_timeframe = _mapping(context.get("multi_timeframe"))
    decision = _mapping(context.get("quant_decision"))
    summary_state = _first_present(multi_timeframe, "summary_state", "state")
    action = _first_present(decision, "action", "order_type")
    confidence = _first_present(decision, "confidence")
    subject = f"{symbol} {name}".strip() or "当前股票"
    parts = [f"{subject}的结构化量化结果"]
    if summary_state is not None:
        parts.append(f"综合状态 {summary_state}")
    if action is not None:
        parts.append(f"量化决策 {action}")
    if confidence is not None:
        parts.append(f"置信度 {confidence}")
    return "；".join(parts) + "。"


def _local_result(context: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    decision = _mapping(context.get("quant_decision"))
    result = _result_base(
        status="ok",
        source="local",
        generated_at=generated_at,
        raw_response="",
    )
    summary = _local_summary(context)
    result.update(
        {
            "summary": summary,
            "cycle_explanation": _local_cycle_explanation(context),
            "scenarios": [
                {
                    "name": "基准情景",
                    "description": (
                        f"量化决策状态为 {_first_present(decision, 'action', 'order_type') or 'UNKNOWN'}，"
                        "仅在结构化触发条件满足时观察后续确认。"
                    ),
                }
            ],
            "risks": _condition_list(
                _first_present(decision, "risk_flags", "risks", "risk_warnings")
            ),
            "trigger_conditions": _condition_list(
                _first_present(decision, "trigger_conditions", "triggers")
            ),
            "invalid_conditions": _condition_list(
                _first_present(decision, "invalid_conditions", "invalidation")
            ),
            "notification_text": summary,
        }
    )
    return result


def _provider_from_settings(settings: Any) -> AIProviderSettings:
    provider = getattr(settings, "provider", settings)
    if isinstance(provider, AIProviderSettings):
        return provider
    if isinstance(provider, Mapping):
        return AIProviderSettings(
            model=str(provider.get("model") or "deepseek-chat"),
            base_url=str(provider.get("base_url") or "https://api.deepseek.com/v1").rstrip("/"),
            api_key=str(provider.get("api_key") or ""),
            thinking=bool(provider.get("thinking") or False),
            reasoning_effort=str(provider.get("reasoning_effort") or "medium"),
            context_window=int(provider.get("context_window") or 128000),
            proxy_url=str(provider.get("proxy_url") or ""),
            timeout_seconds=float(provider.get("timeout_seconds") or 60.0),
        )
    return provider


def run_quant_analysis(
    context: Mapping[str, Any],
    settings: AISettings | AIProviderSettings,
) -> dict[str, Any]:
    generated_at = _generated_at(str(context.get("generated_at") or ""))
    provider = _provider_from_settings(settings)
    if not str(provider.api_key or "").strip():
        return _local_result(context, generated_at)

    raw_response = ""
    try:
        messages = build_quant_analysis_prompt(context)
        raw_response = str(call_chat_completion(provider, messages))
        payload = extract_json_object(raw_response)
        if payload is None:
            raise ValueError("模型响应不是有效的 JSON 对象")
        return _normalize_model_result(
            payload,
            generated_at=generated_at,
            raw_response=raw_response,
        )
    except Exception as exc:  # noqa: BLE001 - callers must receive a stable error payload
        result = _result_base(
            status="error",
            source="model",
            generated_at=generated_at,
            raw_response=raw_response,
        )
        result["summary"] = f"AI 分析失败：{exc}"
        result["notification_text"] = "AI 结构化分析暂不可用。"
        return result


__all__ = [
    "build_quant_analysis_context",
    "build_quant_analysis_prompt",
    "run_quant_analysis",
]

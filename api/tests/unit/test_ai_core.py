from __future__ import annotations

import json

import httpx

from xquant.ai.service import (
    _client,
    build_decision_tree_layout,
    build_snapshot,
    build_stage1_prompt,
    mask_provider,
    normalize_ai_settings,
    run_two_stage,
    stream_two_stage,
)
from xquant.marketdata.remote import (
    RemoteImportRequest,
    fetch_remote_bars,
    normalize_remote_payload,
)
from xquant.marketdata.synthetic import generate_synthetic_bars
from xquant.notifications.feishu import sign_feishu_payload


def _bars() -> list[dict[str, str | float]]:
    return [
        {
            "session_id": bar.session_id,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume_shares,
        }
        for bar in generate_synthetic_bars("DEMO.RESEARCH", n=180, seed=7)
    ]


def test_stage1_prompt_contains_core_context() -> None:
    settings = normalize_ai_settings({"analysis_bar_count": 120})
    snapshot = build_snapshot(
        dataset_id="d1",
        symbol="DEMO.RESEARCH",
        timeframe="1d",
        bars=_bars(),
        settings=settings,
    )
    prompt = build_stage1_prompt(snapshot)
    text = " ".join(item["content"] for item in prompt)
    assert "当前趋势" in text
    assert "当前周期位置" in text
    assert "候选支撑" in text
    assert "候选阻力" in text
    assert "K1" in text


def test_local_two_stage_builds_decision_and_tree() -> None:
    settings = normalize_ai_settings({"analysis_bar_count": 120, "decision_stance": "balanced"})
    snapshot = build_snapshot(
        dataset_id="d1",
        symbol="DEMO.RESEARCH",
        timeframe="1d",
        bars=_bars(),
        settings=settings,
    )
    record = run_two_stage(snapshot, settings)
    assert record["status"] == "ok"
    assert record["stage1_diagnosis"]["diagnosis_summary"]
    assert record["stage2_decision"]["decision"]["action"] in {"LONG", "SHORT", "WAIT"}
    assert record["decision_tree_layout"]["nodes"]
    assert record["raw_prompt"]["stage1"]
    assert record["raw_prompt"]["stage2"]


def test_tree_layout_and_provider_masking() -> None:
    layout = build_decision_tree_layout(
        {"current_trend": {"direction": "bullish"}, "current_cycle": "channel"},
        {"decision": {"action": "LONG"}, "future_trend": {"label": "向上"}},
    )
    assert [node["id"] for node in layout["nodes"]] == ["n1", "n2", "n3", "n4", "n5", "n6"]
    provider = mask_provider({"api_key": "secret-key", "model": "demo"})
    assert provider["api_key"] == "***"
    assert "secret-key" not in str(provider)


def test_socks_proxy_client_is_supported() -> None:
    settings = normalize_ai_settings(
        {"provider": {"proxy_url": "socks5://127.0.0.1:1080"}}
    )
    client = _client(settings.provider)
    client.close()


def test_stream_retries_without_unsupported_optional_fields(monkeypatch) -> None:
    settings = normalize_ai_settings(
        {
            "provider": {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://model.test/v1",
                "thinking": True,
            },
            "analysis_bar_count": 120,
        }
    )
    snapshot = build_snapshot(
        dataset_id="d1",
        symbol="DEMO.RESEARCH",
        timeframe="1d",
        bars=_bars(),
        settings=settings,
    )
    requests: list[dict] = []
    accepted = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal accepted
        payload = json.loads(request.content)
        requests.append(payload)
        if "stream_options" in payload or "reasoning_effort" in payload:
            return httpx.Response(
                400,
                json={"error": {"message": "unsupported optional field"}},
            )
        accepted += 1
        if accepted == 1:
            content = {
                "current_trend": {"direction": "bullish"},
                "current_cycle": "markup",
                "next_cycle": "distribution",
                "diagnosis_summary": "test diagnosis",
                "confidence": 70,
            }
        else:
            content = {
                "decision": {"action": "WAIT", "confidence": 60, "reasoning": "test"},
                "future_trend": {"label": "range"},
                "next_cycle_prediction": {"cycle": "range"},
                "next_bar_prediction": {"direction": "neutral"},
            }
        body = (
            "data: "
            + json.dumps(
                {
                    "id": "request-1",
                    "model": "test-model",
                    "choices": [
                        {
                            "delta": {"content": json.dumps(content, ensure_ascii=False)},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            + "\n\n"
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    monkeypatch.setattr(
        "xquant.ai.service._client",
        lambda _provider: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    events = list(stream_two_stage(snapshot, settings))

    assert events[-1]["type"] == "done"
    assert events[-1]["record"]["status"] == "ok"
    assert len(requests) == 6
    assert sum("stream_options" not in payload for payload in requests) == 4
    assert (
        sum(
            "stream_options" not in payload and "reasoning_effort" not in payload
            for payload in requests
        )
        == 2
    )


def test_stream_falls_back_when_provider_ignores_stream_flag(monkeypatch) -> None:
    settings = normalize_ai_settings(
        {
            "provider": {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://model.test/v1",
            },
            "analysis_bar_count": 120,
        }
    )
    snapshot = build_snapshot(
        dataset_id="d1",
        symbol="DEMO.RESEARCH",
        timeframe="1d",
        bars=_bars(),
        settings=settings,
    )
    fallback_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fallback_calls
        payload = json.loads(request.content)
        if payload.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"choices": [{"message": {"content": ""}}]},
            )
        fallback_calls += 1
        content = (
            {
                "current_trend": {"direction": "bullish"},
                "current_cycle": "markup",
                "next_cycle": "distribution",
                "diagnosis_summary": "fallback diagnosis",
                "confidence": 70,
            }
            if fallback_calls == 1
            else {
                "decision": {"action": "WAIT", "confidence": 60, "reasoning": "fallback"},
                "future_trend": {"label": "range"},
                "next_cycle_prediction": {"cycle": "range"},
                "next_bar_prediction": {"direction": "neutral"},
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "fallback-request",
                "model": "test-model",
                "choices": [
                    {
                        "message": {"content": json.dumps(content, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "xquant.ai.service._client",
        lambda _provider: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    events = list(stream_two_stage(snapshot, settings))

    assert fallback_calls == 2
    assert events[-1]["type"] == "done"
    assert events[-1]["record"]["status"] == "ok"
    assert events[-1]["record"]["stage1_diagnosis"]["diagnosis_summary"] == "fallback diagnosis"
    assert any("切换普通请求重试" in event.get("text", "") for event in events)


def test_stream_always_finishes_after_unexpected_exception(monkeypatch) -> None:
    settings = normalize_ai_settings(
        {
            "provider": {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "https://model.test/v1",
            },
            "analysis_bar_count": 120,
        }
    )
    snapshot = build_snapshot(
        dataset_id="d1",
        symbol="DEMO.RESEARCH",
        timeframe="1d",
        bars=_bars(),
        settings=settings,
    )

    def fail_stream(*_args, **_kwargs):
        raise RuntimeError("unexpected stream failure")

    monkeypatch.setattr("xquant.ai.service._consume_stream_reply", fail_stream)

    events = list(stream_two_stage(snapshot, settings))

    assert [event["type"] for event in events[-2:]] == ["error", "done"]
    assert events[-1]["record"]["status"] == "error"
    assert events[-1]["record"]["exception"]["stage"] == "stage1"


def test_remote_payload_normalization_and_feishu_signing() -> None:
    bars = normalize_remote_payload(
        [
            {
                "session_id": "2026-01-02",
                "open": 10,
                "high": 10,
                "low": 9,
                "close": 9.5,
                "volume": 100,
            },
            {
                "session_id": "2026-01-01",
                "open": 10,
                "high": 10,
                "low": 9,
                "close": 9.5,
                "volume": 100,
            },
            {
                "session_id": "2026-01-03",
                "open": 9.5,
                "high": 9.4,
                "low": 9.5,
                "close": 9.5,
                "volume": 100,
            },
            {
                "session_id": "2026-01-04",
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "volume": None,
            },
        ]
    )
    assert [bar["session_id"] for bar in bars] == ["2026-01-01", "2026-01-02"]
    signed = sign_feishu_payload({"msg_type": "text"}, "secret")
    assert signed["timestamp"]
    assert signed["sign"]


def test_yahoo_fetch_encodes_symbol_and_normalizes_bars(monkeypatch) -> None:
    requests: list[tuple[str, dict]] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            timestamps = [1_767_225_600_000 + day * 86_400_000 for day in range(80)]
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": timestamps,
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100] * 80,
                                        "high": [101] * 80,
                                        "low": [99] * 80,
                                        "close": [100.5] * 80,
                                        "volume": [1_000] * 80,
                                    }
                                ]
                            },
                        }
                    ]
                }
            }

    def fake_get(
        url: str,
        params: dict,
        timeout: float,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> FakeResponse:
        requests.append((url, params))
        return FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    request = RemoteImportRequest(source="yfinance", symbol="GC=F", timeframe="1d", lookback=80)
    result = fetch_remote_bars(request)

    assert requests[0][0] == "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF"
    assert result["symbol"] == "GC=F"
    assert result["source_provider"] == "yfinance_public_chart"
    assert requests[0][1]["interval"] == "1d"
    assert requests[0][1]["range"] == "2y"
    # Yahoo marks the latest intraday bar unclosed; normalization removes it.
    assert len(result["bars"]) == 79

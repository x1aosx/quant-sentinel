from __future__ import annotations

from xquant.ai.service import (
    build_decision_tree_layout,
    build_snapshot,
    build_stage1_prompt,
    mask_provider,
    normalize_ai_settings,
    run_two_stage,
)
from xquant.marketdata.remote import normalize_remote_payload
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
        ]
    )
    assert [bar["session_id"] for bar in bars] == ["2026-01-01", "2026-01-02"]
    signed = sign_feishu_payload({"msg_type": "text"}, "secret")
    assert signed["timestamp"]
    assert signed["sign"]

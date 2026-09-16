from __future__ import annotations

import json

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.api.routes.ai import _sse_payload
from xquant.notifications.feishu import build_feishu_card, send_feishu_message
from xquant.system_config import SystemConfigStore


def _create_sample_dataset(client: TestClient) -> str:
    response = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def test_system_config_is_masked_and_masked_values_are_preserved(tmp_path) -> None:
    store = SystemConfigStore(tmp_path / "system-settings.json")
    store.update(
        {
            "provider": {
                "api_key": "sk-live",
                "proxy_url": "http://user:pass@127.0.0.1:7890",
            },
            "feishu": {
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/secret-token",
                "secret": "signing-secret",
            },
        }
    )
    public = store.public_payload()
    assert public["provider"]["api_key"] == "***"
    assert "user:pass" not in public["provider"]["proxy_url"]
    assert public["feishu"]["secret"] == "***"
    assert "secret-token" not in public["feishu"]["webhook_url"]

    store.update(
        {
            "provider": {"api_key": "***", "proxy_url": public["provider"]["proxy_url"]},
            "feishu": {
                "webhook_url": public["feishu"]["webhook_url"],
                "secret": "***",
            },
        }
    )
    assert store.settings.provider.api_key == "sk-live"
    assert store.settings.provider.proxy_url == "http://user:pass@127.0.0.1:7890"
    assert store.settings.feishu.secret == "signing-secret"


def test_feishu_card_contains_decision_future_and_threshold_rule() -> None:
    record = {
        "symbol": "XAUUSD",
        "timeframe": "15m",
        "stage1_diagnosis": {"diagnosis_summary": "趋势向上"},
        "stage2_decision": {
            "decision": {
                "action": "LONG",
                "confidence": 72,
                "entry_price": 2400,
                "stop_loss_price": 2380,
                "take_profit_price": 2450,
                "reasoning": "回踩确认",
            },
            "future_trend": {"label": "偏多"},
            "next_cycle_prediction": {"cycle": "趋势延续"},
            "next_bar_prediction": {"direction": "up"},
        },
    }
    card = build_feishu_card(record)
    text = str(card)
    assert "XAUUSD" in text
    assert "LONG" in text
    assert "偏多" in text
    assert "2400" in text

    skipped = send_feishu_message(
        record,
        webhook_url="https://example.test/hook",
        confidence_threshold=80,
        transport=lambda *_args: (_ for _ in ()).throw(AssertionError("should not send")),
    )
    assert skipped["sent"] is False
    assert "阈值" in skipped["reason"]


def test_batch_and_monitor_endpoints_use_local_research_mode(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        dataset_id = _create_sample_dataset(client)
        batch = client.post(
            "/api/v1/ai/batch/analyze",
            json={"targets": [{"dataset_id": dataset_id}], "concurrency": 2},
        )
        assert batch.status_code == 200, batch.text
        payload = batch.json()
        assert payload["summary"]["succeeded"] == 1
        assert payload["items"][0]["record"]["decision_tree_layout"]["nodes"]
        batch_history = client.get(
            "/api/v1/ai/records",
            params={"dataset_id": dataset_id},
        )
        assert batch_history.status_code == 200, batch_history.text
        assert batch_history.json()["items"][0]["dataset_id"] == dataset_id

        started = client.post(
            "/api/v1/ai/monitor/start",
            json={
                "targets": [{"dataset_id": dataset_id}],
                "interval_seconds": 3600,
                "auto_notify": False,
            },
        )
        assert started.status_code == 200, started.text
        started_payload = started.json()
        assert started_payload["running"] is True
        assert started_payload["schedule_active_now"] is True
        assert started_payload["schedule"]["mode"] == "always"
        assert started_payload["next_check_at"]

        status = client.get("/api/v1/ai/monitor/status")
        assert status.status_code == 200
        assert status.json()["items"][0]["target"]["dataset_id"] == dataset_id
        assert status.json()["schedule_label"] == "24小时盯盘"

        stopped = client.post("/api/v1/ai/monitor/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False


def test_ai_analysis_accepts_stock_timeframe_and_filters_history(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        _create_sample_dataset(client)
        intraday = client.post(
            "/api/v1/datasets/sample",
            json={"timeframe": "15m"},
        )
        assert intraday.status_code == 200, intraday.text

        response = client.post(
            "/api/v1/ai/analyze",
            json={"symbol": "DEMO.RESEARCH", "timeframe": "15m"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["symbol"] == "DEMO.RESEARCH"
        assert response.json()["timeframe"] == "15m"

        history = client.get(
            "/api/v1/ai/records",
            params={"symbol": "DEMO.RESEARCH", "timeframe": "15m"},
        )
        assert history.status_code == 200, history.text
        items = history.json()["items"]
        assert len(items) == 1
        assert items[0]["dataset_id"] == intraday.json()["id"]
        assert items[0]["timeframe"] == "15m"


def test_ai_records_list_all_symbols_with_pagination(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        database = client.app.state.db
        for record_id, symbol, created_at in (
            ("record-old", "AAA", "2026-01-01T00:00:00+00:00"),
            ("record-new", "BBB", "2026-01-01T00:00:01+00:00"),
        ):
            database.save_analysis_record(
                {
                    "id": record_id,
                    "symbol": symbol,
                    "timeframe": "1d",
                    "status": "ok",
                    "created_at": created_at,
                    "stage2_decision": {
                        "decision": {
                            "action": "LONG",
                            "confidence": 75,
                        }
                    },
                }
            )

        first_page = client.get(
            "/api/v1/ai/records",
            params={"limit": 1, "offset": 0},
        )
        second_page = client.get(
            "/api/v1/ai/records",
            params={"limit": 1, "offset": 1},
        )

        assert first_page.status_code == 200, first_page.text
        assert second_page.status_code == 200, second_page.text
        first_payload = first_page.json()
        second_payload = second_page.json()
        assert first_payload["total"] == 2
        assert first_payload["limit"] == 1
        assert first_payload["offset"] == 0
        assert first_payload["items"][0]["symbol"] == "BBB"
        assert second_payload["total"] == 2
        assert second_payload["offset"] == 1
        assert second_payload["items"][0]["symbol"] == "AAA"


def test_streaming_endpoint_returns_final_record(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        dataset_id = _create_sample_dataset(client)
        response = client.post(
            "/api/v1/ai/analyze/stream",
            json={"dataset_id": dataset_id},
        )
        assert response.status_code == 200, response.text
        events = [
            json.loads(line[5:].strip())
            for chunk in response.text.split("\n\n")
            for line in chunk.splitlines()
            if line.startswith("data:")
        ]
        assert events[-1]["type"] == "done"
        assert events[-1]["record"]["decision_tree_layout"]
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.headers["x-accel-buffering"] == "no"

        history = client.get("/api/v1/ai/records", params={"dataset_id": dataset_id})
        assert history.status_code == 200, history.text
        saved = history.json()["items"]
        assert len(saved) == 1
        assert saved[0]["dataset_id"] == dataset_id
        assert saved[0]["symbol"] == "DEMO.RESEARCH"

        detail = client.get(f"/api/v1/ai/records/{saved[0]['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["record"]["decision_tree_layout"]["nodes"]
        assert detail.json()["dataset_id"] == dataset_id

        missing = client.get("/api/v1/ai/records/missing")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "分析记录不存在"


def test_sse_payload_replaces_non_finite_numbers() -> None:
    payload = _sse_payload(
        {"type": "done", "record": {"value": float("nan"), "nested": [float("inf")]}}
    )
    event = json.loads(payload.removeprefix("data: ").strip())
    assert event["record"] == {"value": None, "nested": [None]}

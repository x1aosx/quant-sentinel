from __future__ import annotations

from fastapi.testclient import TestClient

from xquant.api.app import create_app


def _create_sample_timeframes(client: TestClient, timeframes: list[str]) -> None:
    for timeframe in timeframes:
        response = client.post("/api/v1/datasets/sample", json={"timeframe": timeframe})
        assert response.status_code == 200, response.text


def test_stock_analysis_aggregates_timeframes_and_local_ai(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        _create_sample_timeframes(client, ["1d", "1h", "30m", "15m"])

        response = client.get("/api/v1/stocks/DEMO.RESEARCH/analysis")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["symbol"] == "DEMO.RESEARCH"
        assert set(payload["timeframes"]) == {"1d", "1h", "30m", "15m"}
        assert payload["multi_timeframe"]["missing_timeframes"] == []
        assert payload["multi_timeframe"]["metadata"]["profile"]["strategic"] == "1d"
        assert payload["decision"]["action"] in {
            "AVOID",
            "WATCH",
            "WAIT_BUY",
            "BUY",
            "HOLD",
            "REDUCE",
            "WAIT_SELL",
            "SELL",
        }
        assert payload["ai_summary"] is None

        ai_response = client.post("/api/v1/stocks/DEMO.RESEARCH/analysis/ai", json={})
        assert ai_response.status_code == 200, ai_response.text
        ai_payload = ai_response.json()
        assert ai_payload["status"] == "ok"
        assert ai_payload["source"] == "local"
        assert ai_payload["summary"]

        cached_ai = client.get(
            "/api/v1/stocks/DEMO.RESEARCH/analysis",
            params={"include_ai": "true"},
        )
        assert cached_ai.status_code == 200, cached_ai.text
        assert cached_ai.json()["ai_summary"]["source"] == "local"


def test_stock_analysis_tolerates_missing_timeframes(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        _create_sample_timeframes(client, ["1d", "15m"])

        response = client.get("/api/v1/stocks/DEMO.RESEARCH/analysis")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload["timeframes"]) == {"1d", "15m"}
        assert set(payload["multi_timeframe"]["missing_timeframes"]) == {"1h", "30m"}
        assert payload["multi_timeframe"]["conflict_score"] >= 0

        timeframe_response = client.get(
            "/api/v1/stocks/DEMO.RESEARCH/analysis/timeframes/15m"
        )
        assert timeframe_response.status_code == 200, timeframe_response.text
        assert timeframe_response.json()["timeframe"] == "15m"

        missing = client.get("/api/v1/stocks/DEMO.RESEARCH/analysis/timeframes/1h")
        assert missing.status_code == 404

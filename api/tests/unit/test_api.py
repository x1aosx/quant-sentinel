from __future__ import annotations

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.marketdata.remote import RemoteImportRequest


def test_dataset_and_analysis_endpoints(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        sample = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
        assert sample.status_code == 200, sample.text
        dataset = sample.json()
        assert dataset["symbol"] == "DEMO.RESEARCH"
        assert dataset["bar_count"] == 180

        datasets = client.get("/api/v1/datasets")
        assert datasets.status_code == 200
        assert [item["id"] for item in datasets.json()["items"]] == [dataset["id"]]

        sr = client.post(
            "/api/v1/analysis/support-resistance",
            json={"dataset_id": dataset["id"], "lookback": 120, "n_zones": 6, "direction": "both"},
        )
        assert sr.status_code == 200, sr.text
        sr_result = sr.json()
        assert sr_result["bars_used"] == 120
        assert len(sr_result["candles"]) == 150
        assert "levels" in sr_result

        pa = client.post(
            "/api/v1/analysis/price-action",
            json={"dataset_id": dataset["id"], "lookback": 120, "stance": "conservative"},
        )
        assert pa.status_code == 200, pa.text
        pa_result = pa.json()
        assert pa_result["bars_used"] == 120
        assert pa_result["simulation_only"] is True
        assert pa_result["decision"]["action"] in {"LONG", "SHORT", "WAIT"}
        assert len(pa_result["candles"]) == 150


def test_dataset_validation_rejects_short_and_bad_bars(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        short = client.post(
            "/api/v1/datasets",
            json={
                "symbol": "TEST",
                "timeframe": "1d",
                "bars": [
                    {"session_id": str(i), "open": 10, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1}
                    for i in range(10)
                ],
            },
        )
        assert short.status_code == 400
        assert "至少需要 60" in short.json()["detail"]

        bad = client.post(
            "/api/v1/datasets",
            json={
                "symbol": "TEST",
                "timeframe": "1d",
                "bars": [
                    {"session_id": str(i), "open": 10, "high": 9, "low": 8, "close": 9.5, "volume": 1}
                    for i in range(60)
                ],
            },
        )
        assert bad.status_code == 400


def test_remote_dataset_import_uses_provider_payload_and_stores_dataset(
    tmp_path, monkeypatch
) -> None:
    request_seen = None

    def fake_fetch_remote_bars(payload: dict) -> dict:
        nonlocal request_seen
        request_seen = RemoteImportRequest(
            source=str(payload["source"]),
            symbol=str(payload["symbol"]),
            timeframe=str(payload["timeframe"]),
            lookback=int(payload["lookback"]),
            adjust=str(payload["adjust"]),
        )
        return {
            "symbol": request_seen.symbol.upper(),
            "timeframe": request_seen.timeframe,
            "source": request_seen.source,
            "source_provider": "yfinance_public_chart",
            "simulation_only": True,
            "bars": [
                {
                    "session_id": f"2026-01-{day:02d}",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                    "volume": 1_000,
                }
                for day in range(1, 81)
            ],
        }

    monkeypatch.setattr("xquant.api.app.fetch_remote_bars", fake_fetch_remote_bars)

    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        response = client.post(
            "/api/v1/datasets/remote",
            json={
                "source": "yfinance",
                "symbol": "gc=f",
                "timeframe": "1d",
                "lookback": 500,
                "adjust": "qfq",
            },
        )

        assert response.status_code == 200, response.text
        created = response.json()
        assert created["symbol"] == "GC=F"
        assert created["bar_count"] == 80
        assert created["source"] == "yfinance"
        assert created["source_provider"] == "yfinance_public_chart"
        assert request_seen is not None
        assert request_seen.symbol.upper() == "GC=F"

        datasets = client.get("/api/v1/datasets").json()["items"]
        assert [item["id"] for item in datasets] == [created["id"]]

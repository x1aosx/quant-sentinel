from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.marketdata.remote import RemoteImportRequest


def test_dataset_and_analysis_endpoints(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        storage = client.get("/api/v1/health/storage")
        assert storage.status_code == 200
        assert storage.json()["status"] == "ok"

        sample = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
        assert sample.status_code == 200, sample.text
        dataset = sample.json()
        assert dataset["symbol"] == "DEMO.RESEARCH"
        assert dataset["bar_count"] == 180

        dashboard = client.get("/api/v1/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["dataset_count"] == 1

        datasets = client.get("/api/v1/datasets")
        assert datasets.status_code == 200
        assert [item["id"] for item in datasets.json()["items"]] == [dataset["id"]]

        fetched_dataset = client.get(f"/api/v1/datasets/{dataset['id']}")
        assert fetched_dataset.status_code == 200
        assert fetched_dataset.json() == dataset

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

        missing = client.post(
            "/api/v1/analysis/support-resistance",
            json={"dataset_id": "missing", "lookback": 120},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "数据集不存在"


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

    monkeypatch.setattr("xquant.api.routes.datasets.fetch_remote_bars", fake_fetch_remote_bars)

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


def test_quotes_download_returns_deterministic_csv(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        response = client.get(
            "/api/v1/marketdata/quotes/download",
            params={"symbol": "DEMO.RESEARCH", "timeframe": "1d", "count": 60},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == (
            'attachment; filename="DEMO.RESEARCH_1d.csv"'
        )

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 60
        assert list(rows[0]) == ["session_id", "open", "high", "low", "close", "volume"]
        assert rows[0]["session_id"] == "S0001"
        assert rows[-1]["session_id"] == "S0060"
        assert all(float(row["open"]) > 0 for row in rows)

        repeat = client.get(
            "/api/v1/marketdata/quotes/download",
            params={"symbol": "DEMO.RESEARCH", "timeframe": "1d", "count": 60},
        )
        assert repeat.status_code == 200
        assert repeat.text == response.text


def test_quotes_download_rejects_invalid_input(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        blank_symbol = client.get(
            "/api/v1/marketdata/quotes/download",
            params={"symbol": "", "timeframe": "1d"},
        )
        assert blank_symbol.status_code == 400

        invalid_count = client.get(
            "/api/v1/marketdata/quotes/download",
            params={"symbol": "DEMO.RESEARCH", "timeframe": "1d", "count": 0},
        )
        assert invalid_count.status_code == 422

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from xquant.api.app import create_app


def test_alpha_lab_api_training_strategy_backtest_and_realtime(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        dataset = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
        assert dataset.status_code == 200, dataset.text
        dataset_id = dataset.json()["id"]
        dataset_title = dataset.json()["title"]

        overview = client.get("/api/v1/alphalab/overview")
        assert overview.status_code == 200, overview.text
        assert "training" in overview.json()

        created = client.post(
            "/api/v1/alphalab/training/runs",
            json={
                "data_snapshot_id": dataset_id,
                "total_steps": 1,
                "batch_size": 2,
                "seed": 7,
                "min_bars": 60,
            },
        )
        assert created.status_code == 200, created.text
        created_run = created.json()
        run_id = created_run["id"]
        assert created_run["name"] == dataset_title
        assert created_run["dataset_id"] == dataset_id
        assert created_run["symbol"] == "DEMO.RESEARCH"
        assert created_run["timeframe"] == "1d"

        run = created_run
        deadline = time.time() + 10
        while time.time() < deadline:
            response = client.get(f"/api/v1/alphalab/training/runs/{run_id}")
            assert response.status_code == 200, response.text
            run = response.json()
            if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.05)
        assert run["status"] == "SUCCEEDED", run
        assert run["name"] == dataset_title
        assert run["dataset_id"] == dataset_id

        strategies = client.get("/api/v1/alphalab/strategies")
        assert strategies.status_code == 200, strategies.text
        items = strategies.json()["items"]
        assert items
        strategy = items[0]
        assert strategy["name"] == dataset_title
        assert strategy["data_snapshot_id"] == dataset_id

        backtest = client.post(
            "/api/v1/alphalab/backtests",
            json={
                "strategy_id": strategy["strategy_id"],
                "initial_capital": 100_000,
                "commission_pct": 0.03,
                "slippage_pct": 0.02,
            },
        )
        assert backtest.status_code == 200, backtest.text
        assert backtest.json()["dataset_id"] == dataset_id
        assert backtest.json()["metrics"]["trade_count"] >= 0

        watch = client.post(
            "/api/v1/alphalab/realtime/watches",
            json={
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["version"],
                "source": "local",
                "symbol": "DEMO.RESEARCH",
                "timeframe": "1d",
            },
        )
        assert watch.status_code == 200, watch.text
        watch_id = watch.json()["id"]

        evaluated = client.post(
            "/api/v1/alphalab/realtime/evaluate",
            json={"watch_id": watch_id},
        )
        assert evaluated.status_code == 200, evaluated.text
        assert evaluated.json()["evaluated"] == 1

        signals = client.get(
            "/api/v1/alphalab/realtime/signals",
            params={"watch_id": watch_id},
        )
        assert signals.status_code == 200, signals.text
        assert isinstance(signals.json()["items"], list)

        deleted = client.delete(f"/api/v1/alphalab/realtime/watches/{watch_id}")
        assert deleted.status_code == 200, deleted.text

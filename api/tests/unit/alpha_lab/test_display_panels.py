"""AlphaLab 展示面板所需的接口字段：训练曲线/日志、回测绩效/资金曲线、监控约束。

这些用例只覆盖本次新增的对外契约：
- 训练任务返回 ``metrics_history`` 与 ``logs``；
- 回测结果返回对齐的 ``time_axis`` / ``rolling_sharpe`` 与交易级绩效指标；
- 实时监控必须绑定已有策略的品种与周期，并回传策略名。
"""

from __future__ import annotations

import math
import time

from fastapi.testclient import TestClient

from xquant.api.app import create_app


def _wait_for_run(client: TestClient, run_id: str, *, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    run: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/v1/alphalab/training/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"training run did not finish: {run}")


def _bootstrap(client: TestClient, *, total_steps: int = 3) -> tuple[str, dict]:
    dataset = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
    assert dataset.status_code == 200, dataset.text
    dataset_id = dataset.json()["id"]

    created = client.post(
        "/api/v1/alphalab/training/runs",
        json={
            "data_snapshot_id": dataset_id,
            "total_steps": total_steps,
            "batch_size": 2,
            "seed": 7,
            "min_bars": 60,
        },
    )
    assert created.status_code == 200, created.text
    run = _wait_for_run(client, created.json()["id"])
    assert run["status"] == "SUCCEEDED", run

    strategies = client.get("/api/v1/alphalab/strategies")
    assert strategies.status_code == 200, strategies.text
    strategy = strategies.json()["items"][0]
    return dataset_id, {"run": run, "strategy": strategy}


def test_training_run_exposes_curve_and_logs(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        dataset_id, context = _bootstrap(client, total_steps=3)
        run = context["run"]

        history = run["metrics_history"]
        assert isinstance(history, list)
        assert len(history) == 3, history
        assert [point["step"] for point in history] == [1, 2, 3]
        for point in history:
            # 训练曲线的每一列都必须是有限数值，前端才能直接画图。
            for key in ("reward", "validation_score", "best_score", "ic", "entropy"):
                assert math.isfinite(float(point[key])), (key, point)

        logs = run["logs"]
        assert isinstance(logs, list) and logs
        for entry in logs:
            assert entry["level"] in {"info", "warn", "error"}, entry
            assert entry["ts"]
            assert entry["message"]
        messages = " ".join(entry["message"] for entry in logs)
        assert "训练任务已创建" in messages
        assert "训练完成" in messages

        assert run["dataset_title"] == "研究示例数据"
        assert run["config_json"]["symbol"] == "DEMO.RESEARCH"
        assert run["config_json"]["bars"] > 0

        # 列表接口与详情接口必须给出一致的曲线字段。
        listed = client.get("/api/v1/alphalab/training/runs").json()["items"]
        listed_run = next(item for item in listed if item["id"] == run["id"])
        assert len(listed_run["metrics_history"]) == 3
        assert listed_run["logs"]
        assert listed_run["dataset_id"] == dataset_id


def test_backtest_exposes_performance_detail_and_equity_axis(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        dataset_id, context = _bootstrap(client)
        strategy = context["strategy"]

        response = client.post(
            "/api/v1/alphalab/backtests",
            json={
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["version"],
                "initial_capital": 100_000,
                "commission_pct": 0.03,
                "slippage_pct": 0.02,
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "SUCCEEDED"
        assert result["dataset_id"] == dataset_id
        assert result["strategy_name"] == strategy["name"]

        metrics = result["metrics"]
        for key in (
            "total_return",
            "annualized_return",
            "sharpe",
            "sortino",
            "calmar",
            "max_drawdown",
            "win_rate",
            "profit_loss_ratio",
            "profit_factor",
            "average_holding_bars",
            "exposure_mean",
            "exposure_max",
            "trade_count",
        ):
            assert key in metrics, key
            assert math.isfinite(float(metrics[key])), (key, metrics[key])
        assert 0.0 <= float(metrics["win_rate"]) <= 1.0
        assert float(metrics["exposure_max"]) <= 1.0 + 1e-9

        equity = result["equity_curve"]
        axis = result["time_axis"]
        drawdown = result["drawdown_curve"]
        rolling = result["rolling_sharpe"]
        assert len(equity) == len(axis) == len(drawdown) == len(rolling) > 0
        assert result["time_axis_kind"] == "session"
        # 样本数据集的 session_id 形如 S0001，必须原样透出而不是合成日期。
        assert axis[0] == "S0001"
        assert all(math.isfinite(float(value)) for value in rolling)

        for trade in result["trade_log"]:
            assert trade["side"] in {"long", "short"}
            assert math.isfinite(float(trade["return_pct"]))
            assert trade["entry_session"]
            assert trade["exit_session"]

        # 绩效明细按品种展示，单品种行同样要有交易级指标。
        per_symbol = result["per_symbol"]
        assert per_symbol
        for item in per_symbol:
            assert item["symbol"] == "DEMO.RESEARCH"
            for key in ("sortino", "win_rate", "profit_loss_ratio", "exposure_max"):
                assert key in item["metrics"], key
                assert math.isfinite(float(item["metrics"][key]))

        # 落盘后由列表接口读回的结果必须保留同样的字段。
        listed = client.get("/api/v1/alphalab/backtests").json()["items"]
        stored = next(item for item in listed if item["run_id"] == result["run_id"])
        assert stored["time_axis"] == axis
        assert stored["rolling_sharpe"] == rolling
        assert stored["metrics"]["sortino"] == metrics["sortino"]


def test_realtime_watch_requires_existing_strategy_symbol(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        _dataset_id, context = _bootstrap(client)
        strategy = context["strategy"]
        assert strategy["symbol"] == "DEMO.RESEARCH"
        assert strategy["timeframe"] == "1d"

        mismatched_symbol = client.post(
            "/api/v1/alphalab/realtime/watches",
            json={
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["version"],
                "source": "market_data",
                "symbol": "600519.SH",
                "timeframe": "1d",
            },
        )
        assert mismatched_symbol.status_code == 400, mismatched_symbol.text
        assert "600519.SH" in mismatched_symbol.json()["detail"]

        mismatched_timeframe = client.post(
            "/api/v1/alphalab/realtime/watches",
            json={
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["version"],
                "source": "market_data",
                "symbol": "DEMO.RESEARCH",
                "timeframe": "1h",
            },
        )
        assert mismatched_timeframe.status_code == 400, mismatched_timeframe.text

        unknown_strategy = client.post(
            "/api/v1/alphalab/realtime/watches",
            json={
                "strategy_id": "no-such-strategy",
                "symbol": "DEMO.RESEARCH",
                "timeframe": "1d",
            },
        )
        assert unknown_strategy.status_code == 400, unknown_strategy.text

        created = client.post(
            "/api/v1/alphalab/realtime/watches",
            json={
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["version"],
                "source": "market_data",
                "symbol": "DEMO.RESEARCH",
                "timeframe": "1d",
            },
        )
        assert created.status_code == 200, created.text
        watch = created.json()
        assert watch["strategy_name"] == strategy["name"]
        assert watch["symbol"] == "DEMO.RESEARCH"

        watches = client.get("/api/v1/alphalab/realtime/watches").json()["items"]
        assert watches and all(item["strategy_name"] for item in watches)

        evaluated = client.post(
            "/api/v1/alphalab/realtime/evaluate",
            json={"watch_id": watch["id"]},
        )
        assert evaluated.status_code == 200, evaluated.text
        signals = evaluated.json()["signals"]
        assert signals and signals[0]["strategy_name"] == strategy["name"]

        listed_signals = client.get(
            "/api/v1/alphalab/realtime/signals",
            params={"watch_id": watch["id"]},
        )
        assert listed_signals.status_code == 200, listed_signals.text
        for signal in listed_signals.json()["items"]:
            assert signal["strategy_name"] == strategy["name"]

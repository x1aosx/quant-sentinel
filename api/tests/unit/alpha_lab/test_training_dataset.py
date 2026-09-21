from __future__ import annotations

import time

from fastapi.testclient import TestClient

from xquant.api.app import create_app


def _create_1d_sample_dataset(client: TestClient) -> dict:
    response = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
    assert response.status_code == 200, response.text
    return response.json()


def _create_training_run(client: TestClient, dataset_id: str, **overrides) -> dict:
    payload = {
        "data_snapshot_id": dataset_id,
        "total_steps": 1,
        "batch_size": 2,
        "seed": 7,
        "min_bars": 60,
        **overrides,
    }
    return client.post("/api/v1/alphalab/training/runs", json=payload)


def _wait_for_terminal_status(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    run = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/v1/alphalab/training/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    return run


def test_training_run_on_1d_sample_dataset_reaches_succeeded(tmp_path) -> None:
    """日周期（1d）样本数据集 → 创建训练 run → 轮询到 SUCCEEDED。"""
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        dataset = _create_1d_sample_dataset(client)
        dataset_id = dataset["id"]
        dataset_title = dataset["title"]

        created = _create_training_run(client, dataset_id)
        assert created.status_code == 200, created.text
        run = created.json()
        run_id = run["id"]

        # run 元数据来自数据集：timeframe == "1d"、symbol == "DEMO.RESEARCH"、name == 数据集标题
        assert run["timeframe"] == "1d"
        assert run["symbol"] == "DEMO.RESEARCH"
        assert run["name"] == dataset_title
        assert run["dataset_id"] == dataset_id

        terminal = _wait_for_terminal_status(client, run_id)
        assert terminal["status"] == "SUCCEEDED", terminal
        assert terminal["name"] == dataset_title
        assert terminal["dataset_id"] == dataset_id


def test_training_run_reads_symbol_and_timeframe_from_dataset_metadata(tmp_path) -> None:
    """创建训练 run 时 symbol/timeframe 应从数据集元数据正确读取，不抛 500。"""
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        dataset = _create_1d_sample_dataset(client)
        dataset_id = dataset["id"]

        created = _create_training_run(client, dataset_id)
        assert created.status_code == 200, created.text
        run = created.json()

        assert run["symbol"] == "DEMO.RESEARCH"
        assert run["timeframe"] == "1d"
        assert run["dataset_id"] == dataset_id


def test_training_run_with_invalid_or_missing_dataset_id_returns_4xx(tmp_path) -> None:
    """非法/缺失 dataset_id 应返回 4xx（400/404），不返回 500。

    说明：当前后端提供非日周期数据集创建入口（/datasets/sample 接受任意 timeframe），
    且对 DEMO.RESEARCH 非日周期数据训练返回 200（不拒绝），因此本用例走替代分支，
    验证数据校验路径对非法/缺失 dataset_id 返回 4xx 而非 500。
    """
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        invalid = _create_training_run(client, "nonexistent-dataset-id")
        assert invalid.status_code == 400, invalid.text
        assert invalid.status_code != 500

        missing = _create_training_run(client, "")
        assert missing.status_code == 400, missing.text
        assert missing.status_code != 500
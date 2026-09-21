"""因子训练最少 bar 数阈值与 walk-forward 可行性校验的回归测试。

覆盖 runtime.TrainingManager.create_run 的两层校验：
1. minimum_bars 快速失败（默认取 MiningSettings.training_min_bars，默认 300）；
2. minimum_bars 通过后的 walk-forward 可行性校验（非 DEMO.RESEARCH symbol）。

DEMO.RESEARCH 样本数据集对两层校验均豁免，用于保证样本流程不被默认阈值破坏。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

from xquant.alpha_lab.config import MiningSettings
from xquant.alpha_lab.data import BarFrame, XQSMarketDataAdapter
from xquant.api.app import create_app

_TEST_SYMBOL = "TEST.STK"


def _make_frame(n_bars: int, symbol: str = _TEST_SYMBOL, timeframe: str = "1d") -> BarFrame:
    """构造单 symbol、指定 bar 数的合法日线 BarFrame。"""
    close = np.linspace(100.0, 200.0, n_bars, dtype=np.float64)
    return BarFrame(
        symbol=symbol,
        timeframe=timeframe,
        open=close[None, :],
        high=(close + 1.0)[None, :],
        low=(close - 1.0)[None, :],
        close=close[None, :],
        volume=np.full((1, n_bars), 1_000_000.0, dtype=np.float64),
        time=np.arange(n_bars, dtype=np.float64)[None, :],
        is_closed=np.ones((1, n_bars), dtype=bool),
        source="test",
        adjustment="none",
    )


@contextmanager
def _fake_market_data(
    n_bars: int,
    symbol: str = _TEST_SYMBOL,
    timeframe: str = "1d",
) -> Iterator[None]:
    """用指定 bar 数的非 DEMO symbol 数据集替换市场数据适配器。

    样本数据集固定为 DEMO.RESEARCH（豁免分支），无法构造 <300 根 bar 的日线数据，
    因此直接 mock XQSMarketDataAdapter 的元数据与 bar 加载。
    """
    frame = _make_frame(n_bars, symbol, timeframe)
    metadata = {
        "symbol": symbol,
        "timeframe": timeframe,
        "title": f"{symbol} {timeframe}",
    }
    with mock.patch.object(
        XQSMarketDataAdapter, "get_dataset_metadata", return_value=metadata
    ), mock.patch.object(XQSMarketDataAdapter, "load_bars", return_value=frame):
        yield


def _create_run(client: TestClient, **overrides):
    payload = {
        "data_snapshot_id": "test-dataset",
        "total_steps": 1,
        "batch_size": 2,
        "seed": 7,
        **overrides,
    }
    return client.post("/api/v1/alphalab/training/runs", json=payload)


def _wait_for_terminal_status(
    client: TestClient, run_id: str, timeout: float = 10.0
) -> dict:
    run: dict = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/v1/alphalab/training/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    return run


def test_default_training_min_bars_is_300() -> None:
    """默认阈值从 3000 修正为 300。"""
    assert MiningSettings().training_min_bars == 300


def test_default_min_bars_rejects_dataset_below_threshold(tmp_path) -> None:
    """299 根 bar、非 DEMO symbol、未显式指定 min_bars → 400 insufficient。"""
    with _fake_market_data(299), TestClient(create_app(tmp_path / "xquant.db")) as client:
        response = _create_run(client)
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "insufficient" in detail.lower(), detail
        assert "299/300" in detail, detail


def test_dataset_at_default_threshold_is_accepted(tmp_path) -> None:
    """300 根 bar、非 DEMO symbol、未显式指定 min_bars → 200 且训练到 SUCCEEDED。"""
    with _fake_market_data(300), TestClient(create_app(tmp_path / "xquant.db")) as client:
        created = _create_run(client)
        assert created.status_code == 200, created.text
        run = created.json()
        assert run["symbol"] == _TEST_SYMBOL

        terminal = _wait_for_terminal_status(client, run["id"])
        assert terminal["status"] == "SUCCEEDED", terminal


def test_sub_300_walk_forward_feasible_accepted_with_explicit_min_bars(tmp_path) -> None:
    """250 根 bar（<300）但 walk-forward 可行，显式 min_bars=200 覆盖默认阈值 → 200。

    说明：实现中 minimum_bars 是硬门槛，低于默认 300 的数据集必须通过 payload 显式
    降低 min_bars；此处同时验证「显式 min_bars 仍生效」与「200-299 根可完成训练」。
    """
    with _fake_market_data(250), TestClient(create_app(tmp_path / "xquant.db")) as client:
        created = _create_run(client, min_bars=200)
        assert created.status_code == 200, created.text
        run = created.json()

        terminal = _wait_for_terminal_status(client, run["id"])
        assert terminal["status"] == "SUCCEEDED", terminal


def test_walk_forward_infeasible_dataset_rejected(tmp_path) -> None:
    """5 根 bar、min_bars=1 通过快速失败，但 walk-forward 不可行 → 400 too short。

    这是新增的 walk-forward 可行性校验层：即使显式把 min_bars 降到 1，也必须拒绝
    无法构建任何自适应折的数据集，避免训练在后台以 "sample is too short" 失败。
    """
    with _fake_market_data(5), TestClient(create_app(tmp_path / "xquant.db")) as client:
        response = _create_run(client, min_bars=1)
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "too short" in detail.lower(), detail


def test_very_small_dataset_rejected_as_insufficient(tmp_path) -> None:
    """40 根 bar（<50）、未显式指定 min_bars → 400 且 detail 含 insufficient。"""
    with _fake_market_data(40), TestClient(create_app(tmp_path / "xquant.db")) as client:
        response = _create_run(client)
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "insufficient" in detail.lower(), detail
        assert "40/300" in detail, detail


def test_demo_research_sample_dataset_exempt_from_default_min_bars(tmp_path) -> None:
    """DEMO.RESEARCH 样本数据集对默认 300 阈值豁免，未显式指定 min_bars 仍可训练。"""
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        dataset = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
        assert dataset.status_code == 200, dataset.text
        dataset_id = dataset.json()["id"]

        created = client.post(
            "/api/v1/alphalab/training/runs",
            json={
                "data_snapshot_id": dataset_id,
                "total_steps": 1,
                "batch_size": 2,
                "seed": 7,
            },
        )
        assert created.status_code == 200, created.text
        run = created.json()
        assert run["symbol"] == "DEMO.RESEARCH"

        terminal = _wait_for_terminal_status(client, run["id"])
        assert terminal["status"] == "SUCCEEDED", terminal

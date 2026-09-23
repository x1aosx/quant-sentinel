"""行情存储故障必须返回可读的 503，而不是让前端只看到裸 500。

根因：InfluxDB 3 Core 触发 ``query-file-limit`` 时 ``InfluxDBStore.query`` 抛出的
``httpx.HTTPStatusError`` 逃出了 ``/ai/analyze/stream`` 的 try 块（取数据集发生在
try 之前），Starlette 只能回一个纯文本 500，前端 ``streamAIAnalysis`` 解析不到
任何 ``detail``，页面上只显示 “Internal Server Error”。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.storage import InfluxDBQueryError

UPSTREAM_DETAIL = (
    "Query would scan 432 Parquet files, exceeding the file limit. "
    "Use a narrower time range, or increase the limit with --query-file-limit"
)


@pytest.fixture
def app(tmp_path) -> FastAPI:
    return create_app(tmp_path / "xquant.db")


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _fail_dataset_fetch(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """让所有走 InfluxDB 取 bar 的路径都失败，模拟存储不可用。"""

    def _boom(_dataset_id: str) -> dict[str, object]:
        raise InfluxDBQueryError("query", UPSTREAM_DETAIL, status_code=500)

    monkeypatch.setattr(app.state.db, "get_dataset", _boom)


def test_ai_stream_reports_storage_failure_as_readable_503(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_dataset_fetch(app, monkeypatch)

    response = client.post("/api/v1/ai/analyze/stream", json={"dataset_id": "any-dataset"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "InfluxDB" in detail
    assert "HTTP 500" in detail
    assert "432 Parquet files" in detail


def test_ai_analyze_reports_storage_failure_as_readable_503(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_dataset_fetch(app, monkeypatch)

    response = client.post("/api/v1/ai/analyze", json={"dataset_id": "any-dataset"})

    assert response.status_code == 503
    assert "432 Parquet files" in response.json()["detail"]


def test_dataset_detail_reports_storage_failure_as_readable_503(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_dataset_fetch(app, monkeypatch)

    response = client.get("/api/v1/datasets/any-dataset")

    assert response.status_code == 503
    assert "432 Parquet files" in response.json()["detail"]


def test_unreachable_storage_reports_503_without_upstream_status(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_dataset_id: str) -> dict[str, object]:
        raise InfluxDBQueryError("query", "ConnectError: connection refused")

    monkeypatch.setattr(app.state.db, "get_dataset", _boom)

    response = client.get("/api/v1/datasets/any-dataset")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "connection refused" in detail
    assert "HTTP 500" not in detail

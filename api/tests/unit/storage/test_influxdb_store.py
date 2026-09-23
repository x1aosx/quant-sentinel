from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from xquant.storage.influxdb import InfluxDBQueryError, InfluxDBStore
from xquant.storage.settings import InfluxSettings


def test_point_to_line_protocol() -> None:
    point = {
        "measurement": "market bar",
        "tags": {"dataset_id": "dataset,1", "symbol": "TEST"},
        "fields": {
            "session_id": "2026-01-01",
            "source_seq": 1,
            "open": 10.0,
            "complete": True,
        },
        "time": datetime(2026, 1, 1, tzinfo=UTC),
    }

    line = InfluxDBStore.point_to_line_protocol(point)

    assert line.startswith('market\\ bar,dataset_id=dataset\\,1,symbol=TEST ')
    assert 'session_id="2026-01-01"' in line
    assert "source_seq=1i" in line
    assert "open=10.0" in line
    assert "complete=true" in line
    assert line.endswith(" 1767225600000000000")


def test_influxdb_3_uses_write_lp_and_query_sql_paths() -> None:
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def __init__(self, payload: list[dict[str, Any]] | None = None) -> None:
            self._payload = payload or []

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, Any]]:
            return self._payload

    class FakeClient:
        def post(self, path: str, **kwargs: Any) -> FakeResponse:
            calls.append({"path": path, **kwargs})
            return FakeResponse([{"value": 1}]) if path.endswith("query_sql") else FakeResponse()

        def close(self) -> None:
            return None

    store = InfluxDBStore(
        InfluxSettings(
            url="http://influx:8181",
            database="quant-sentinel",
            token=SecretStr("secret"),
        ),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    written = store.write_line_protocol(["market_bar value=1i"])
    rows = store.query("SELECT 1 AS value", {"dataset_id": "dataset-1"})

    assert written == 1
    assert rows == [{"value": 1}]
    assert calls[0]["path"] == "/api/v3/write_lp"
    assert calls[1]["path"] == "/api/v3/query_sql"
    assert calls[1]["json"]["format"] == "json"
    assert calls[1]["headers"] == {"Accept": "application/json"}


def _status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://influx:8181/api/v3/query_sql")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError("upstream error", request=request, response=response)


class _FailingClient:
    """抛出指定异常的 httpx.Client 替身。"""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def post(self, _path: str, **_kwargs: Any) -> Any:
        raise self._error

    def close(self) -> None:
        return None


def _store(error: Exception) -> InfluxDBStore:
    return InfluxDBStore(
        InfluxSettings(
            url="http://influx:8181", database="quant-sentinel", token=SecretStr("secret")
        ),
        client=_FailingClient(error),  # type: ignore[arg-type]
    )


def test_query_turns_upstream_500_into_readable_error() -> None:
    upstream = _status_error(
        500,
        "External error: Query would scan 432 Parquet files, exceeding the file limit.",
    )

    with pytest.raises(InfluxDBQueryError) as excinfo:
        _store(upstream).query("SELECT 1", {"dataset_id": "dataset-1"})

    assert excinfo.value.status_code == 500
    assert "432 Parquet files" in excinfo.value.detail
    assert str(excinfo.value).startswith("InfluxDB query failed (HTTP 500):")


def test_write_turns_upstream_500_into_readable_error() -> None:
    with pytest.raises(InfluxDBQueryError) as excinfo:
        _store(_status_error(500, "write rejected")).write_line_protocol(["market_bar value=1i"])

    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "write rejected"
    assert str(excinfo.value).startswith("InfluxDB write failed (HTTP 500):")


def test_unreachable_influxdb_reports_without_status_code() -> None:
    with pytest.raises(InfluxDBQueryError) as excinfo:
        _store(httpx.ConnectError("connection refused")).query("SELECT 1")

    assert excinfo.value.status_code is None
    assert "ConnectError" in excinfo.value.detail
    assert "connection refused" in excinfo.value.detail

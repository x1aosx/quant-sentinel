from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from xquant.storage.influxdb import InfluxDBStore
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
            token="secret",
        ),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    written = store.write_line_protocol(["market_bar value=1i"])
    rows = store.query("SELECT 1 AS value", {"dataset_id": "dataset-1"})
    store.delete_market_bars("dataset-1")

    assert written == 1
    assert rows == [{"value": 1}]
    assert calls[0]["path"] == "/api/v3/write_lp"
    assert calls[1]["path"] == "/api/v3/query_sql"
    assert calls[1]["json"]["format"] == "json"
    assert calls[1]["headers"] == {"Accept": "application/json"}
    assert calls[2]["path"] == "/api/v3/delete"
    assert calls[2]["json"] == {
        "db": "quant-sentinel",
        "measurement": "market_bar",
        "predicate": "dataset_id = 'dataset-1'",
    }

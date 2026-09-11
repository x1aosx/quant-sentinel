from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from xquant.marketdata.remote import RemoteImportRequest, fetch_remote_bars
from xquant.registry import database as database_module
from xquant.registry.database import Database
from xquant.storage import StorageSettings


class FakePostgres:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
        *,
        fetch: str = "none",
    ) -> None:
        values = dict(params or {})
        self.statements.append((statement, values))
        if "INSERT INTO research.dataset" not in statement:
            return
        dataset_id = str(values["id"])
        existing = self.rows.get(dataset_id)
        row = {**existing, **values} if existing else values
        if existing and "created_at = excluded.created_at" not in statement:
            row["created_at"] = existing["created_at"]
        self.rows[dataset_id] = row

    def query(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [self._summary(row) for row in self.rows.values()]

    def query_one(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        values = params or {}
        if "WHERE id" in statement:
            row = self.rows.get(str(values["dataset_id"]))
            return self._summary(row) if row else None
        if "UPPER(symbol)" in statement:
            matches = [
                row
                for row in self.rows.values()
                if str(row["symbol"]).upper() == str(values["symbol"]).upper()
                and row["timeframe"] == values["timeframe"]
            ]
            if not matches:
                return None
            return self._summary(max(matches, key=lambda row: row["created_at"]))
        return None

    def close(self) -> None:
        return None

    @staticmethod
    def _summary(row: dict[str, Any] | None) -> dict[str, Any]:
        assert row is not None
        return {
            **row,
            "source": row.get("source") or "local",
            "source_provider": row.get("source_provider") or "local_file",
            "last_synced_at": row.get("last_synced_at") or row["created_at"],
        }


class FakeInflux:
    def __init__(self) -> None:
        self.points: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.write_batches: list[list[dict[str, Any]]] = []

    def write_points(self, points: list[dict[str, Any]]) -> int:
        self.write_batches.append(deepcopy(points))
        for point in points:
            tags = tuple(sorted(point.get("tags", {}).items()))
            key = (point["measurement"], tags, point["time"])
            self.points[key] = deepcopy(point)
        return len(points)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        dataset_id = str((params or {})["dataset_id"])
        rows = [
            dict(point["fields"])
            for point in self.points.values()
            if point["tags"]["dataset_id"] == dataset_id
        ]
        return sorted(
            rows,
            key=lambda row: (str(row["session_id"]), int(row.get("source_seq", 0))),
        )

    def close(self) -> None:
        return None


def _bar(day: int, close: float = 100.0) -> dict[str, Any]:
    return {
        "session_id": f"2026-01-{day:02d}",
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1_000 + day,
    }


def _remote_payload(*bars: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": "GC=F",
        "timeframe": "1d",
        "source": "yfinance",
        "source_provider": "yfinance_public_chart",
        "simulation_only": True,
        "bars": [deepcopy(bar) for bar in bars],
    }


def _database() -> tuple[Database, FakePostgres, FakeInflux]:
    postgres = FakePostgres()
    influx = FakeInflux()
    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=postgres,
        redis_store=None,
        influx=influx,
    )
    return database, postgres, influx


def test_remote_sync_is_incremental_and_reuses_dataset_metadata(monkeypatch) -> None:
    database, _postgres, influx = _database()
    responses = iter(
        [
            _remote_payload(_bar(1), _bar(2), _bar(3)),
            _remote_payload(_bar(2), _bar(3), _bar(4)),
            _remote_payload(_bar(2), _bar(3), _bar(4)),
        ]
    )
    calls: list[dict[str, Any]] = []

    def fake_fetch_remote_bars(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(deepcopy(payload))
        return next(responses)

    monkeypatch.setattr(database_module, "fetch_remote_bars", fake_fetch_remote_bars)

    first = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 250}
    )
    assert first["inserted_count"] == 3
    assert first["updated_count"] == 0
    assert first["total_count"] == 3
    assert first["bar_count"] == 3
    assert first["first_session"] == "2026-01-01"
    assert first["last_session"] == "2026-01-03"
    assert first["sync_status"] == "updated"
    assert first["synced_at"].endswith("+00:00")
    assert first["dataset"]["source"] == "yfinance"
    assert first["dataset"]["source_provider"] == "yfinance_public_chart"
    assert first["dataset"]["last_synced_at"] is not None
    assert "session_start" not in calls[0]

    second = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 250}
    )
    assert second["id"] == first["id"]
    assert second["inserted_count"] == 1
    assert second["updated_count"] == 2
    assert second["total_count"] == 4
    assert second["bar_count"] == 4
    assert second["first_session"] == "2026-01-01"
    assert second["last_session"] == "2026-01-04"
    assert second["sync_status"] == "updated"
    assert calls[1]["session_start"] == "2026-01-03"
    assert len(influx.points) == 4
    assert [len(batch) for batch in influx.write_batches] == [3, 1]

    third = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 250}
    )
    assert third["id"] == first["id"]
    assert third["inserted_count"] == 0
    assert third["updated_count"] == 3
    assert third["total_count"] == 4
    assert third["sync_status"] == "unchanged"
    assert len(influx.points) == 4
    assert [len(batch) for batch in influx.write_batches] == [3, 1]


def test_first_sync_dataset_id_is_deterministic(monkeypatch) -> None:
    first_database, _first_postgres, _first_influx = _database()
    second_database, _second_postgres, _second_influx = _database()
    responses = iter([_remote_payload(_bar(1)), _remote_payload(_bar(1))])

    def fake_fetch_remote_bars(payload: dict[str, Any]) -> dict[str, Any]:
        return next(responses)

    monkeypatch.setattr(database_module, "fetch_remote_bars", fake_fetch_remote_bars)

    first = first_database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1D", "lookback": 250}
    )
    second = second_database.sync_dataset(
        {"source": "yfinance", "symbol": "GC=F", "timeframe": "1d", "lookback": 250}
    )

    assert first["id"] == second["id"]


def test_dataset_writer_uses_stable_session_timestamp() -> None:
    database, _postgres, influx = _database()
    created_at = datetime(2026, 2, 1, tzinfo=UTC)
    first = _bar(1)
    second = _bar(2)
    third = _bar(3)

    database._write_dataset_bars("dataset-1", "TEST", "1d", [first, second, third], created_at)
    database._write_dataset_bars("dataset-1", "TEST", "1d", [third], created_at)

    first_time = next(
        point["time"]
        for point in influx.write_batches[0]
        if point["fields"]["session_id"] == third["session_id"]
    )
    second_time = influx.write_batches[1][0]["time"]
    assert second_time == first_time
    assert len(influx.points) == 3


def test_existing_dataset_metadata_defaults_are_compatible() -> None:
    database, postgres, _influx = _database()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    postgres.rows["legacy-id"] = {
        "id": "legacy-id",
        "symbol": "LEGACY",
        "timeframe": "1d",
        "bar_count": 0,
        "first_session": "",
        "last_session": "",
        "created_at": created_at,
    }

    summary = database.get_dataset("legacy-id")["summary"]

    assert summary["source"] == "local"
    assert summary["source_provider"] == "local_file"
    assert summary["last_synced_at"] == created_at


def test_yahoo_incremental_request_uses_session_start(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1_767_225_600_000, 1_767_312_000_000],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100, 101],
                                        "high": [102, 103],
                                        "low": [99, 100],
                                        "close": [101, 102],
                                        "volume": [1_000, 1_100],
                                    }
                                ]
                            },
                        }
                    ]
                }
            }

    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> FakeResponse:
        requests.append(params)
        return FakeResponse()

    monkeypatch.setattr("xquant.marketdata.remote.httpx.get", fake_get)
    result = fetch_remote_bars(
        RemoteImportRequest(
            source="yfinance",
            symbol="GC=F",
            timeframe="1d",
            lookback=10,
            session_start="2026-01-01",
            session_end="2026-01-03",
        )
    )

    assert requests[0]["period1"] == "1767225600"
    assert requests[0]["period2"] == "1767398400"
    assert "range" not in requests[0]
    assert result["bars"][0]["session_id"] == "2026-01-01T00:00:00+00:00"


def test_yahoo_incremental_request_defaults_end_to_now(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1_767_225_600_000, 1_767_312_000_000],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100, 101],
                                        "high": [102, 103],
                                        "low": [99, 100],
                                        "close": [101, 102],
                                        "volume": [1_000, 1_100],
                                    }
                                ]
                            },
                        }
                    ]
                }
            }

    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> FakeResponse:
        requests.append(params)
        return FakeResponse()

    monkeypatch.setattr("xquant.marketdata.remote.httpx.get", fake_get)
    result = fetch_remote_bars(
        RemoteImportRequest(
            source="yfinance",
            symbol="GC=F",
            timeframe="1d",
            lookback=10,
            session_start="2026-01-01",
        )
    )

    assert requests[0]["period1"] == "1767225600"
    assert int(requests[0]["period2"]) > int(requests[0]["period1"])
    assert "range" not in requests[0]
    assert result["bars"][0]["session_id"] == "2026-01-01T00:00:00+00:00"


def test_yahoo_seconds_timestamp_is_not_divided_by_one_thousand(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1_767_225_600, 1_767_312_000],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100, 101],
                                        "high": [102, 103],
                                        "low": [99, 100],
                                        "close": [101, 102],
                                        "volume": [1_000, 1_100],
                                    }
                                ]
                            },
                        }
                    ]
                }
            }

    def fake_get(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("xquant.marketdata.remote.httpx.get", fake_get)
    result = fetch_remote_bars(
        RemoteImportRequest(source="yfinance", symbol="GC=F", timeframe="1d", lookback=10)
    )

    assert result["bars"][0]["session_id"] == "2026-01-01T00:00:00+00:00"

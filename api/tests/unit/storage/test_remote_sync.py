from __future__ import annotations

import sqlite3
import sys
import types
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from xquant.marketdata import remote as remote_module
from xquant.marketdata.remote import (
    RemoteImportRequest,
    fetch_remote_bars,
    resolve_instrument_title,
)
from xquant.registry import database as database_module
from xquant.registry.database import Database
from xquant.registry.sqlite import Database as LegacySqliteDatabase
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
        if "SET deleted_at = now()" in statement:
            dataset_id = str(values["dataset_id"])
            if dataset_id in self.rows:
                self.rows[dataset_id]["deleted_at"] = "deleted"
            return
        if "UPDATE research.dataset" in statement:
            dataset_id = str(values["dataset_id"])
            if dataset_id in self.rows:
                self.rows[dataset_id]["title"] = values["title"]
            return
        if "INSERT INTO research.dataset" not in statement:
            return
        dataset_id = str(values["id"])
        existing = self.rows.get(dataset_id)
        row = {**existing, **values} if existing else values
        if existing and "created_at = excluded.created_at" not in statement:
            row["created_at"] = existing["created_at"]
        if existing and "deleted_at = NULL" in statement:
            row.pop("deleted_at", None)
        self.rows[dataset_id] = row

    def query(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [
            self._summary(row)
            for row in self.rows.values()
            if not row.get("deleted_at")
        ]

    def query_one(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        values = params or {}
        if "WHERE id" in statement:
            row = self.rows.get(str(values["dataset_id"]))
            if row and "deleted_at IS NULL" in statement and row.get("deleted_at"):
                return None
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
            "stored_title": row.get("title"),
            "title": row.get("title") or row["symbol"],
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


class FakeRedis:
    def __init__(self) -> None:
        self.deleted_keys: list[tuple[str, ...]] = []
        self.cached: Any | None = None

    def get_json(self, key: str) -> Any | None:
        return self.cached

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        return None

    def delete(self, *keys: str) -> None:
        self.deleted_keys.append(tuple(keys))


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
        "title": "黄金期货",
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
    assert first["dataset"]["title"] == "黄金期货"
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


def test_compact_intraday_sessions_keep_chronological_order() -> None:
    database, _postgres, influx = _database()
    created_at = datetime(2026, 2, 1, tzinfo=UTC)
    bars = [
        {
            "session_id": session_id,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000.0,
        }
        for session_id in ("202608251000", "202608251030", "202608251100")
    ]

    database._write_dataset_bars("dataset-1", "600519", "30m", bars, created_at)

    written = influx.write_batches[0]
    assert [point["fields"]["session_id"] for point in written] == [
        "202608251000",
        "202608251030",
        "202608251100",
    ]
    assert [point["time"] for point in written] == sorted(point["time"] for point in written)

    influx.points.clear()
    for session_id, stored_at in (
        ("202608251000", datetime(2026, 2, 1, 0, 0, 3, tzinfo=UTC)),
        ("202608251030", datetime(2026, 2, 1, 0, 0, 2, tzinfo=UTC)),
        ("202608251100", datetime(2026, 2, 1, 0, 0, 1, tzinfo=UTC)),
    ):
        influx.points[
            (
                "market_bar",
                (("dataset_id", "dataset-1"),),
                stored_at,
            )
        ] = {
            "measurement": "market_bar",
            "tags": {"dataset_id": "dataset-1"},
            "fields": {
                "session_id": session_id,
                "source_seq": 0,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1_000.0,
            },
            "time": stored_at,
        }

    fetched = database._fetch_dataset_bars("dataset-1")

    assert [bar["session_id"] for bar in fetched] == [
        "202608251000",
        "202608251030",
        "202608251100",
    ]


def test_dataset_read_sorts_legacy_cached_sessions() -> None:
    postgres = FakePostgres()
    influx = FakeInflux()
    redis = FakeRedis()
    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=postgres,
        redis_store=redis,  # type: ignore[arg-type]
        influx=influx,
    )
    bars = [
        {
            "session_id": session_id,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000.0,
        }
        for session_id in ("202608251000", "202608251030", "202608251100")
    ]
    created = database.insert_dataset(
        {
            "symbol": "600519",
            "title": "贵州茅台",
            "timeframe": "30m",
            "bars": bars,
            "created_at": datetime(2026, 2, 1, tzinfo=UTC),
        }
    )
    redis.cached = list(reversed(bars))

    record = database.get_dataset(created["id"])

    assert [bar["session_id"] for bar in record["bars"]] == [
        "202608251000",
        "202608251030",
        "202608251100",
    ]


def test_database_delete_dataset_soft_deletes_metadata_and_cache() -> None:
    postgres = FakePostgres()
    influx = FakeInflux()
    redis = FakeRedis()
    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=postgres,
        redis_store=redis,  # type: ignore[arg-type]
        influx=influx,
    )
    created = database.insert_dataset(
        {
            "symbol": "TEST",
            "title": "测试数据",
            "timeframe": "1d",
            "bars": [_bar(1)],
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )

    deleted = database.delete_dataset(created["id"])

    assert deleted == {"deleted": True, "id": created["id"]}
    assert postgres.rows[created["id"]]["deleted_at"] == "deleted"
    assert influx.points
    assert database.list_datasets() == []
    assert redis.deleted_keys[-1] == (
        "dataset:list",
        f"dataset:{created['id']}:bars",
    )
    with pytest.raises(KeyError):
        database.delete_dataset(created["id"])
    with pytest.raises(KeyError):
        database.delete_dataset("not-a-uuid")


def test_legacy_sqlite_delete_dataset(tmp_path) -> None:
    database = LegacySqliteDatabase(tmp_path / "legacy.db")
    created = database.insert_dataset(
        {
            "symbol": "TEST",
            "title": "测试数据",
            "timeframe": "1d",
            "bars": [_bar(1)],
            "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        }
    )

    deleted = database.delete_dataset(created["id"])

    assert deleted == {"deleted": True, "id": created["id"]}
    assert database.list_datasets() == []
    with pytest.raises(KeyError):
        database.delete_dataset(created["id"])


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
    assert summary["title"] == "LEGACY"
    assert summary["last_synced_at"] == created_at


def test_dataset_titles_refresh_legacy_symbols_without_overwriting_custom_titles() -> None:
    database, postgres, _influx = _database()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    postgres.rows.update(
        {
            "list-symbol": {
                "id": "list-symbol",
                "symbol": "GC=F",
                "title": "GC=F",
                "timeframe": "1d",
                "bar_count": 0,
                "first_session": "",
                "last_session": "",
                "created_at": created_at,
            },
            "list-custom": {
                "id": "list-custom",
                "symbol": "GC=F",
                "title": "我的黄金",
                "timeframe": "1d",
                "bar_count": 0,
                "first_session": "",
                "last_session": "",
                "created_at": created_at,
            },
            "get-symbol": {
                "id": "get-symbol",
                "symbol": "^GSPC",
                "title": "^GSPC",
                "timeframe": "1d",
                "bar_count": 0,
                "first_session": "",
                "last_session": "",
                "created_at": created_at,
            },
            "get-custom": {
                "id": "get-custom",
                "symbol": "^GSPC",
                "title": "我的标普",
                "timeframe": "1d",
                "bar_count": 0,
                "first_session": "",
                "last_session": "",
                "created_at": created_at,
            },
        }
    )

    listed = {dataset["id"]: dataset for dataset in database.list_datasets()}
    listed_symbol = database.get_dataset("get-symbol")["summary"]
    listed_custom = database.get_dataset("get-custom")["summary"]

    assert listed["list-symbol"]["title"] == "黄金期货"
    assert listed["list-custom"]["title"] == "我的黄金"
    assert listed_symbol["title"] == "标普500指数"
    assert listed_custom["title"] == "我的标普"
    assert postgres.rows["list-symbol"]["title"] == "黄金期货"
    assert postgres.rows["list-custom"]["title"] == "我的黄金"
    assert postgres.rows["get-symbol"]["title"] == "标普500指数"
    assert postgres.rows["get-custom"]["title"] == "我的标普"


def test_legacy_sqlite_dataset_titles_refresh_without_overwriting_custom_titles(
    tmp_path,
) -> None:
    database = LegacySqliteDatabase(tmp_path / "legacy.db")
    conn = sqlite3.connect(database.path)
    conn.executemany(
        """
        INSERT INTO datasets
            (id, symbol, title, timeframe, bar_count, first_session, last_session,
             created_at, source, source_provider, exchange, last_synced_at, bars_json)
        VALUES (?, ?, ?, '1d', 0, '', '', ?, 'yfinance', 'yfinance_public_chart',
                NULL, ?, '[]')
        """,
        [
            (
                "list-symbol",
                "GC=F",
                "GC=F",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
            (
                "list-custom",
                "GC=F",
                "我的黄金",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
            (
                "get-symbol",
                "^GSPC",
                "^GSPC",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
            (
                "get-custom",
                "^GSPC",
                "我的标普",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()

    listed = {dataset["id"]: dataset for dataset in database.list_datasets()}
    listed_symbol = database.get_dataset("get-symbol")["summary"]
    listed_custom = database.get_dataset("get-custom")["summary"]

    assert listed["list-symbol"]["title"] == "黄金期货"
    assert listed["list-custom"]["title"] == "我的黄金"
    assert listed_symbol["title"] == "标普500指数"
    assert listed_custom["title"] == "我的标普"
    conn = sqlite3.connect(database.path)
    stored_titles = dict(conn.execute("SELECT id, title FROM datasets"))
    conn.close()
    assert stored_titles["list-symbol"] == "黄金期货"
    assert stored_titles["list-custom"] == "我的黄金"
    assert stored_titles["get-symbol"] == "标普500指数"
    assert stored_titles["get-custom"] == "我的标普"


def test_instrument_title_resolves_local_market_aliases() -> None:
    assert resolve_instrument_title("GC=F") == "黄金期货"
    assert resolve_instrument_title("XAUUSDm") == "黄金/美元"
    assert resolve_instrument_title("^GSPC") == "标普500指数"


def test_instrument_title_uses_eastmoney_exact_code_match(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "QuotationCodeTable": {
                    "Data": [
                        {"Code": "AAPL", "Name": "苹果"},
                        {"Code": "AAPL22", "Name": "Apple Inc Notes 2022"},
                    ]
                }
            }

    def fake_get(*_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(remote_module, "_http_get", fake_get)

    assert resolve_instrument_title("AAPL") == "苹果"


def test_instrument_title_normalizes_hong_kong_symbols(monkeypatch) -> None:
    requested_inputs: list[str] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "QuotationCodeTable": {
                    "Data": [{"Code": "00700", "Name": "腾讯控股"}]
                }
            }

    def fake_get(*_args: Any, **kwargs: Any) -> FakeResponse:
        requested_inputs.append(str(kwargs["params"]["input"]))
        return FakeResponse()

    monkeypatch.setattr(remote_module, "_http_get", fake_get)

    assert resolve_instrument_title("0700.HK") == "腾讯控股"
    assert resolve_instrument_title("00700") == "腾讯控股"
    assert requested_inputs == ["00700", "00700"]


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


def test_remote_request_accepts_tradingview_and_mt5_sources() -> None:
    tradingview = remote_module.validate_remote_request(
        {
            "source": "TradingView",
            "symbol": "800865",
            "timeframe": "1D",
            "lookback": 120,
            "exchange": "bse",
        }
    )
    mt5 = remote_module.validate_remote_request(
        {
            "source": "MT5",
            "symbol": "XAUUSDm",
            "timeframe": "1h",
            "lookback": 120,
        }
    )

    assert tradingview.source == "tradingview"
    assert tradingview.timeframe == "1d"
    assert tradingview.exchange == "BSE"
    assert mt5.source == "mt5"


def test_yahoo_numeric_code_uses_exchange_suffix(monkeypatch) -> None:
    requested_urls: list[str] = []

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

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        requested_urls.append(url)
        return FakeResponse()

    monkeypatch.setattr(remote_module.httpx, "get", fake_get)
    result = fetch_remote_bars(
        RemoteImportRequest(
            source="yfinance",
            symbol="600519",
            timeframe="1d",
            lookback=10,
        )
    )

    assert requested_urls == [
        "https://query1.finance.yahoo.com/v8/finance/chart/600519.SS"
    ]
    assert result["source"] == "yfinance"
    assert result["symbol"] == "600519"


def test_tradingview_source_returns_closed_bars(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, 104.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1_000.0, 1_100.0, 1_200.0],
        },
        index=pd.DatetimeIndex(
            [
                "2026-01-01T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
                "2026-01-03T00:00:00+00:00",
            ],
            name="datetime",
        ),
    )
    calls: list[dict[str, Any]] = []

    class FakeInterval:
        in_daily = "1d"

    class FakeTvDatafeed:
        def __init__(self) -> None:
            self.ws = None

        def get_hist(self, **kwargs: Any) -> pd.DataFrame:
            calls.append(kwargs)
            return frame

    fake_module = types.ModuleType("tvDatafeed")
    fake_module.Interval = FakeInterval
    fake_module.TvDatafeed = FakeTvDatafeed
    monkeypatch.setitem(sys.modules, "tvDatafeed", fake_module)

    result = fetch_remote_bars(
        RemoteImportRequest(
            source="tradingview",
            symbol="800865",
            timeframe="1d",
            lookback=10,
            exchange="BSE",
        )
    )

    assert calls[0]["symbol"] == "800865"
    assert calls[0]["exchange"] == "BSE"
    assert result["source"] == "tradingview"
    assert result["source_provider"] == "tradingview_tvdatafeed"
    assert result["exchange"] == "BSE"
    assert len(result["bars"]) == 2
    assert result["bars"][-1]["session_id"] == "2026-01-02T00:00:00+00:00"


def test_akshare_falls_back_to_tencent_when_eastmoney_disconnects(monkeypatch) -> None:
    requested_urls: list[str] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "code": 0,
                "data": {
                    "sh600519": {
                        "qfqday": [
                            ["2026-01-01", "100", "101", "102", "99", "1000"],
                            ["2026-01-02", "101", "102", "103", "100", "1100"],
                            ["2026-01-03", "102", "103", "104", "101", "1200"],
                        ]
                    }
                },
            }

    def fake_eastmoney(_request: RemoteImportRequest) -> list[dict[str, Any]]:
        raise remote_module.httpx.RemoteProtocolError(
            "Server disconnected without sending a response."
        )

    def fake_get(url: str, **_kwargs: Any) -> FakeResponse:
        requested_urls.append(url)
        return FakeResponse()

    monkeypatch.setattr(remote_module, "_fetch_eastmoney", fake_eastmoney)
    monkeypatch.setattr(remote_module.httpx, "get", fake_get)

    result = fetch_remote_bars(
        RemoteImportRequest(
            source="akshare",
            symbol="600519",
            timeframe="1d",
            lookback=10,
        )
    )

    assert requested_urls == ["https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"]
    assert result["source"] == "akshare"
    assert result["source_provider"] == "tencent_public_kline"
    assert result["fallback_from"] == "eastmoney"
    assert len(result["bars"]) == 2
    assert result["bars"][0]["open"] == 100.0
    assert result["bars"][0]["high"] == 102.0
    assert result["bars"][0]["low"] == 99.0
    assert result["bars"][0]["close"] == 101.0


def test_mt5_source_returns_closed_bars(monkeypatch) -> None:
    shutdown_calls: list[bool] = []

    class FakeMt5:
        TIMEFRAME_D1 = 1440

        @staticmethod
        def initialize() -> bool:
            return True

        @staticmethod
        def symbol_info(symbol: str) -> dict[str, str]:
            return {"name": symbol}

        @staticmethod
        def symbol_select(symbol: str, selected: bool) -> bool:
            return selected

        @staticmethod
        def copy_rates_from_pos(symbol: str, timeframe: int, start: int, count: int):
            return [
                {
                    "time": 1_767_225_600,
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "tick_volume": 1_000,
                },
                {
                    "time": 1_767_312_000,
                    "open": 101,
                    "high": 103,
                    "low": 100,
                    "close": 102,
                    "tick_volume": 1_100,
                },
                {
                    "time": 1_767_398_400,
                    "open": 102,
                    "high": 104,
                    "low": 101,
                    "close": 103,
                    "tick_volume": 1_200,
                },
            ]

        @staticmethod
        def last_error() -> tuple[int, str]:
            return (0, "ok")

        @staticmethod
        def shutdown() -> None:
            shutdown_calls.append(True)

    monkeypatch.setitem(sys.modules, "MetaTrader5", FakeMt5)
    result = fetch_remote_bars(
        RemoteImportRequest(
            source="mt5",
            symbol="XAUUSDm",
            timeframe="1d",
            lookback=10,
        )
    )

    assert shutdown_calls == [True]
    assert result["source"] == "mt5"
    assert result["source_provider"] == "mt5_terminal"
    assert len(result["bars"]) == 2
    assert result["bars"][-1]["session_id"] == "2026-01-02T00:00:00+00:00"


def test_numeric_yfinance_symbol_falls_back_to_tradingview(monkeypatch) -> None:
    def fake_yahoo(_request: RemoteImportRequest) -> list[dict[str, Any]]:
        raise remote_module.RemoteSymbolNotFound("not found")

    def fake_tradingview(
        _request: RemoteImportRequest,
    ) -> tuple[list[dict[str, Any]], str]:
        return ([_bar(1), _bar(2)], "BSE")

    monkeypatch.setattr(remote_module, "_fetch_yahoo", fake_yahoo)
    monkeypatch.setattr(remote_module, "_fetch_tradingview", fake_tradingview)

    result = fetch_remote_bars(
        RemoteImportRequest(
            source="yfinance",
            symbol="800865",
            timeframe="1d",
            lookback=10,
        )
    )

    assert result["source"] == "tradingview"
    assert result["source_provider"] == "tradingview_tvdatafeed"
    assert result["fallback_from"] == "yfinance"
    assert result["exchange"] == "BSE"

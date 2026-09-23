from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from xquant.api.app import create_app
from xquant.api.routes import analysis as analysis_routes
from xquant.registry import Database
from xquant.registry import database as database_module
from xquant.registry.sqlite import Database as LegacySqliteDatabase
from xquant.storage import StorageSettings


def _bars() -> list[dict[str, Any]]:
    return [
        {
            "session_id": f"S{index:04d}",
            "open": 100.0 + index * 0.1,
            "high": 101.0 + index * 0.1,
            "low": 99.0 + index * 0.1,
            "close": 100.5 + index * 0.1,
            "volume": 1_000 + index,
        }
        for index in range(80)
    ]


def _create_dataset(
    client: TestClient,
    *,
    symbol: str,
    title: str,
    timeframe: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/datasets",
        json={
            "symbol": symbol,
            "title": title,
            "timeframe": timeframe,
            "bars": _bars(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_instrument_summaries_cache_filter_pagination_and_invalidation(
    tmp_path,
    monkeypatch,
) -> None:
    summary_calls: list[str] = []
    change_by_symbol = {"AAA": 1.25, "BBB": -0.75, "CCC": 0.0, "DDD": 2.5}
    trend_by_symbol = {
        "AAA": "上涨",
        "BBB": "下跌",
        "CCC": "震荡",
        "DDD": "上涨",
    }

    def fake_summary_item(
        dataset: dict[str, Any],
        _bars: list[dict[str, Any]],
    ) -> dict[str, Any]:
        summary = dataset["summary"]
        symbol = str(summary["symbol"])
        summary_calls.append(symbol)
        return {
            "dataset_id": summary["id"],
            "symbol": symbol,
            "title": summary["title"],
            "timeframe": summary["timeframe"],
            "change_pct": change_by_symbol[symbol],
            "trend": {"label": trend_by_symbol[symbol], "detail": None},
        }

    monkeypatch.setattr(analysis_routes, "_summary_item", fake_summary_item)

    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        created = {
            "AAA": _create_dataset(
                client, symbol="AAA", title="Alpha", timeframe="1d"
            ),
            "BBB": _create_dataset(
                client, symbol="BBB", title="Beta", timeframe="1d"
            ),
            "CCC": _create_dataset(
                client, symbol="CCC", title="Gamma", timeframe="1d"
            ),
        }
        _create_dataset(client, symbol="BBB", title="Beta", timeframe="4h")

        first = client.get("/api/v1/analysis/instruments", params={"page_size": 2})
        assert first.status_code == 200, first.text
        first_payload = first.json()
        assert first_payload["from_cache"] is False
        assert first_payload["count"] == 3
        assert first_payload["total"] == 3
        assert first_payload["page"] == 1
        assert first_payload["page_size"] == 2
        assert first_payload["total_pages"] == 2
        assert first_payload["facets"] == {
            "timeframes": ["4h", "1d"],
            "trends": ["上涨", "下跌", "震荡"],
        }
        assert sorted(summary_calls) == ["AAA", "BBB", "CCC"]

        keyword = client.get(
            "/api/v1/analysis/instruments",
            params={"keyword": "alp", "refresh": False},
        )
        assert keyword.status_code == 200, keyword.text
        keyword_payload = keyword.json()
        assert keyword_payload["from_cache"] is True
        assert keyword_payload["total"] == 1
        assert keyword_payload["count"] == 1
        assert keyword_payload["items"][0]["symbol"] == "AAA"
        assert sorted(summary_calls) == ["AAA", "BBB", "CCC"]

        filtered = client.get(
            "/api/v1/analysis/instruments",
            params={"trend": "下跌", "change": "down"},
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["from_cache"] is True
        assert filtered_payload["total"] == 1
        assert filtered_payload["items"][0]["symbol"] == "BBB"

        second_page = client.get(
            "/api/v1/analysis/instruments",
            params={"page": 2, "page_size": 2},
        )
        assert second_page.status_code == 200, second_page.text
        page_payload = second_page.json()
        assert page_payload["from_cache"] is True
        assert page_payload["count"] == 3
        assert page_payload["total"] == 3
        assert page_payload["total_pages"] == 2

        empty = client.get(
            "/api/v1/analysis/instruments",
            params={"keyword": "missing"},
        )
        assert empty.status_code == 200, empty.text
        empty_payload = empty.json()
        assert empty_payload["items"] == []
        assert empty_payload["count"] == 0
        assert empty_payload["total"] == 0
        assert empty_payload["total_pages"] == 0

        refreshed = client.get(
            "/api/v1/analysis/instruments",
            params={"refresh": "true"},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["from_cache"] is False
        assert len(summary_calls) == 6

        deleted = client.delete(f"/api/v1/datasets/{created['BBB']['id']}")
        assert deleted.status_code == 200, deleted.text
        after_delete = client.get("/api/v1/analysis/instruments")
        assert after_delete.status_code == 200, after_delete.text
        after_delete_payload = after_delete.json()
        assert after_delete_payload["from_cache"] is False
        assert after_delete_payload["total"] == 2
        assert len(summary_calls) == 8

        _create_dataset(client, symbol="DDD", title="Delta", timeframe="1d")
        after_insert = client.get("/api/v1/analysis/instruments")
        assert after_insert.status_code == 200, after_insert.text
        after_insert_payload = after_insert.json()
        assert after_insert_payload["from_cache"] is False
        assert after_insert_payload["total"] == 3
        assert {item["symbol"] for item in after_insert_payload["items"]} == {
            "AAA",
            "CCC",
            "DDD",
        }
        assert len(summary_calls) == 11


def test_legacy_sqlite_json_cache_round_trip(tmp_path) -> None:
    database = LegacySqliteDatabase(tmp_path / "legacy.db")

    database.set_cached_json("snapshot", {"items": [1, 2]}, ttl_seconds=60)

    assert database.get_cached_json("snapshot") == {"items": [1, 2]}
    assert database.get_cached_json("missing") is None


class _FailingRedis:
    def get_json(self, _key: str) -> Any | None:
        raise RedisError("redis unavailable")

    def set_json(self, _key: str, _value: Any, ttl_seconds: int) -> None:
        raise RedisError("redis unavailable")


def test_postgres_cache_degrades_when_redis_is_unavailable() -> None:
    database = Database.__new__(Database)
    database.redis = _FailingRedis()

    assert database.get_cached_json("snapshot") is None
    database.set_cached_json("snapshot", {"items": []}, ttl_seconds=60)
    assert database._cache_get_or_set(
        "snapshot",
        lambda: {"items": [1]},
        ttl_seconds=60,
    ) == {"items": [1]}


class _SyncPostgres:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def execute(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        if "INSERT INTO research.dataset" not in statement:
            return
        values = dict(params or {})
        dataset_id = str(values["id"])
        existing = self.rows.get(dataset_id)
        row = {**existing, **values} if existing else values
        if existing is not None:
            row["created_at"] = existing["created_at"]
        self.rows[dataset_id] = row

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


class _SyncInflux:
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

    def query(
        self,
        _sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._rows(str((params or {})["dataset_id"]))

    def query_time_windows(
        self,
        _sql: str,
        params: dict[str, Any] | None = None,
        *,
        lower: datetime,
        upper: datetime,
    ) -> list[dict[str, Any]]:
        return self._rows(str((params or {})["dataset_id"]), lower, upper)

    def _rows(
        self,
        dataset_id: str,
        lower: datetime | None = None,
        upper: datetime | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            dict(point["fields"])
            for point in self.points.values()
            if point["tags"]["dataset_id"] == dataset_id
            and (lower is None or point["time"] >= lower)
            and (upper is None or point["time"] <= upper)
        ]
        return sorted(
            rows,
            key=lambda row: (str(row["session_id"]), int(row.get("source_seq", 0))),
        )


def _sync_bar(day: int, close: float) -> dict[str, Any]:
    return {
        "session_id": f"2026-01-{day:02d}",
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 1_000 + day,
    }


def _sync_payload(*bars: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": "GC=F",
        "title": "黄金期货",
        "timeframe": "1d",
        "source": "yfinance",
        "source_provider": "yfinance_public_chart",
        "bars": [deepcopy(bar) for bar in bars],
    }


def test_postgres_sync_rewrites_revised_existing_bars(monkeypatch) -> None:
    postgres = _SyncPostgres()
    influx = _SyncInflux()
    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=postgres,
        redis_store=None,
        influx=influx,
    )
    responses = iter(
        [
            _sync_payload(_sync_bar(1, 100.0), _sync_bar(2, 101.0), _sync_bar(3, 102.0)),
            _sync_payload(_sync_bar(1, 100.0), _sync_bar(2, 101.0), _sync_bar(3, 302.0)),
            _sync_payload(_sync_bar(1, 100.0), _sync_bar(2, 101.0), _sync_bar(3, 302.0)),
        ]
    )

    monkeypatch.setattr(database_module, "fetch_remote_bars", lambda _payload: next(responses))

    first = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 250}
    )
    second = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 250}
    )

    assert first["inserted_count"] == 3
    assert first["updated_count"] == 0
    assert first["sync_status"] == "updated"
    assert second["id"] == first["id"]
    assert second["inserted_count"] == 0
    assert second["updated_count"] == 3
    assert second["sync_status"] == "updated"
    assert [len(batch) for batch in influx.write_batches] == [3, 1]
    refreshed_bars = {
        bar["session_id"]: bar for bar in database._fetch_dataset_bars(first["id"])
    }
    assert refreshed_bars["2026-01-03"]["close"] == 302.0

    third = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 250}
    )

    assert third["inserted_count"] == 0
    assert third["updated_count"] == 3
    assert third["sync_status"] == "unchanged"
    assert [len(batch) for batch in influx.write_batches] == [3, 1]

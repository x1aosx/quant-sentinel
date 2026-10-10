"""行情存储（InfluxDB）丢失 ``market_bar`` 表时的降级与重建。

根因：InfluxDB 3 的实例数据目录被重建后表会消失，数据集元数据仍记着
``bar_count``，读 K 线得到 HTTP 400 ``table 'public.iox.market_bar' not found``，
被当成“行情存储不可用”直接 503，AI 两阶段分析因此无法进行。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from xquant.marketdata.repair import repair_dataset_bars
from xquant.registry import database as database_module
from xquant.registry.database import Database
from xquant.storage import InfluxDBQueryError, StorageSettings, is_missing_table_error

MISSING_TABLE_DETAIL = "Error during planning: table 'public.iox.market_bar' not found"
FILE_LIMIT_DETAIL = (
    "Query would scan 432 Parquet files, exceeding the file limit. "
    "Use a narrower time range, or increase the limit with --query-file-limit"
)


class FakeInflux:
    """行情存储替身：``present=False`` 表示表还没建（或已经被清空）。"""

    def __init__(self, *, present: bool = True) -> None:
        self.present = present
        self.points: list[dict[str, Any]] = []
        self.write_batches: list[list[dict[str, Any]]] = []

    def _guard(self) -> None:
        if not self.present:
            raise InfluxDBQueryError("query", MISSING_TABLE_DETAIL, status_code=400)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self._guard()
        return self._rows(str((params or {})["dataset_id"]))

    def query_time_windows(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        lower: datetime,
        upper: datetime,
    ) -> list[dict[str, Any]]:
        self._guard()
        return self._rows(str((params or {})["dataset_id"]), lower, upper)

    def write_points(self, points: list[dict[str, Any]]) -> int:
        self.write_batches.append(deepcopy(points))
        self.present = True
        self.points.extend(deepcopy(point) for point in points)
        return len(points)

    def drop_table(self) -> None:
        self.present = False
        self.points.clear()
        self.write_batches.clear()

    def _rows(
        self,
        dataset_id: str,
        lower: datetime | None = None,
        upper: datetime | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            dict(point["fields"])
            for point in self.points
            if point["tags"]["dataset_id"] == dataset_id
            and (lower is None or point["time"] >= lower)
            and (upper is None or point["time"] <= upper)
        ]
        return sorted(rows, key=lambda row: (str(row["session_id"]), int(row.get("source_seq", 0))))

    def close(self) -> None:
        return None


class FakePostgres:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def execute(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
        *,
        fetch: str = "none",
    ) -> None:
        values = dict(params or {})
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
            return (
                self._summary(max(matches, key=lambda row: row["created_at"])) if matches else None
            )
        return None

    def close(self) -> None:
        return None

    @staticmethod
    def _summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "stored_title": row.get("title"),
            "title": row.get("title") or row["symbol"],
            "source": row.get("source") or "local",
            "source_provider": row.get("source_provider") or "local_file",
            "last_synced_at": row.get("last_synced_at") or row["created_at"],
        }


def _bars(count: int) -> list[dict[str, Any]]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        {
            "session_id": (start + timedelta(days=index)).date().isoformat(),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1_000.0 + index,
        }
        for index in range(count)
    ]


def _remote_payload(bars: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "symbol": "GC=F",
        "title": "黄金期货",
        "timeframe": "1d",
        "source": "yfinance",
        "source_provider": "yfinance_public_chart",
        "simulation_only": True,
        "bars": deepcopy(bars),
    }


def _database(*, present: bool = True) -> tuple[Database, FakePostgres, FakeInflux]:
    influx = FakeInflux(present=present)
    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=FakePostgres(),
        redis_store=None,
        influx=influx,
    )
    return database, database.postgres, influx  # type: ignore[return-value]


def test_missing_table_error_is_recognized() -> None:
    assert is_missing_table_error(
        InfluxDBQueryError("query", MISSING_TABLE_DETAIL, status_code=400)
    )


def test_missing_table_error_is_recognized_without_a_store_exception() -> None:
    # 只按消息兜底也不该把非存储层异常算进来。
    assert not is_missing_table_error(RuntimeError(MISSING_TABLE_DETAIL))


@pytest.mark.parametrize(
    "error",
    [
        InfluxDBQueryError("query", FILE_LIMIT_DETAIL, status_code=500),
        InfluxDBQueryError(
            "query", "query error: database not found: xquant_market", status_code=404
        ),
        # 写失败不能当成「表还不存在」，否则数据丢失会被静默忽略。
        InfluxDBQueryError("write", MISSING_TABLE_DETAIL, status_code=400),
    ],
)
def test_missing_table_error_ignores_other_failures(error: BaseException) -> None:
    assert not is_missing_table_error(error)


def test_dataset_read_degrades_to_empty_when_table_is_missing() -> None:
    database, _postgres, influx = _database(present=False)
    metadata = {
        "id": "dataset-1",
        "bar_count": 500,
        "first_session": "2025-01-01",
        "last_session": "2026-05-15",
        "created_at": datetime(2025, 1, 1, tzinfo=UTC),
        "last_synced_at": datetime(2026, 5, 15, tzinfo=UTC),
    }

    # 带时间边界与不带时间边界的查询都必须降级为空，而不是抛 503 的存储故障。
    assert database._fetch_dataset_bars("dataset-1", metadata) == []
    assert database._fetch_dataset_bars("dataset-1") == []
    assert influx.points == []


def test_dataset_read_still_raises_on_real_storage_failure() -> None:
    class FailingInflux(FakeInflux):
        def _guard(self) -> None:
            raise InfluxDBQueryError("query", FILE_LIMIT_DETAIL, status_code=500)

    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=FakePostgres(),
        redis_store=None,
        influx=FailingInflux(),
    )

    with pytest.raises(InfluxDBQueryError):
        database._fetch_dataset_bars("dataset-1")


def test_sync_rebuilds_lost_bars_instead_of_appending_a_tail(monkeypatch) -> None:
    database, postgres, influx = _database()
    stored = _bars(500)
    monkeypatch.setattr(
        database_module, "fetch_remote_bars", lambda payload: _remote_payload(stored)
    )
    created = database.sync_dataset({"source": "yfinance", "symbol": "gc=f", "timeframe": "1d"})
    assert created["total_count"] == 500
    dataset_id = created["id"]

    # 行情存储被清空：表消失、元数据还在。
    influx.drop_table()
    calls: list[dict[str, Any]] = []

    def fake_fetch(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        return _remote_payload(_bars(200))

    monkeypatch.setattr(database_module, "fetch_remote_bars", fake_fetch)

    result = database.sync_dataset({"source": "yfinance", "symbol": "gc=f", "timeframe": "1d"})

    assert len(calls) == 1
    assert result["id"] == dataset_id
    assert result["rebuilt"] is True
    assert result["total_count"] == 200
    # 重建必须放弃增量窗口，并按元数据里的 bar_count 放大 lookback。
    assert "session_start" not in calls[0]
    assert calls[0]["lookback"] == 500
    assert len(influx.points) == 200
    assert postgres.rows[dataset_id]["bar_count"] == 200
    assert database._fetch_dataset_bars(dataset_id, postgres.rows[dataset_id]) != []


def test_sync_refuses_to_rebuild_when_provider_returns_too_few_bars(monkeypatch) -> None:
    database, postgres, influx = _database()
    monkeypatch.setattr(
        database_module, "fetch_remote_bars", lambda payload: _remote_payload(_bars(500))
    )
    created = database.sync_dataset({"source": "yfinance", "symbol": "gc=f", "timeframe": "1d"})
    dataset_id = created["id"]
    influx.drop_table()

    monkeypatch.setattr(
        database_module, "fetch_remote_bars", lambda payload: _remote_payload(_bars(3))
    )

    with pytest.raises(ValueError, match="不足以重建"):
        database.sync_dataset({"source": "yfinance", "symbol": "gc=f", "timeframe": "1d"})
    # 宁可不写：元数据不能被覆盖成残缺的几根 K 线。
    assert postgres.rows[dataset_id]["bar_count"] == 500
    assert influx.points == []


def test_sync_does_not_refetch_when_the_request_is_already_full_history(monkeypatch) -> None:
    database, _postgres, influx = _database()
    monkeypatch.setattr(
        database_module, "fetch_remote_bars", lambda payload: _remote_payload(_bars(500))
    )
    created = database.sync_dataset({"source": "yfinance", "symbol": "gc=f", "timeframe": "1d"})
    influx.drop_table()

    calls: list[dict[str, Any]] = []

    def fake_fetch(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        return _remote_payload(_bars(400))

    monkeypatch.setattr(database_module, "fetch_remote_bars", fake_fetch)

    # 参数已在第一跳前改成全量：恢复路径不该再回源第二次。
    result = database.sync_dataset(
        {"source": "yfinance", "symbol": "gc=f", "timeframe": "1d", "lookback": 1000}
    )

    assert len(calls) == 1
    assert calls[0]["lookback"] == 1000
    assert result["id"] == created["id"]
    assert result["total_count"] == 400
    assert result["rebuilt"] is True


class StubRepairDatabase:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_symbols: set[str] = set()
        # symbol -> 回源实际拿到的根数；缺省时按请求的 lookback 返回。
        self.restored: dict[str, int] = {}

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "a",
                "symbol": "600105",
                "timeframe": "15m",
                "bar_count": 1000,
                "source": "akshare",
            },
            {"id": "b", "symbol": "DEMO", "timeframe": "1d", "bar_count": 180, "source": "local"},
            {"id": "c", "symbol": "GC=F", "timeframe": "1d", "bar_count": 0, "source": "yfinance"},
        ]

    def sync_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if payload["symbol"] in self.fail_symbols:
            raise ValueError("远程行情数据不足")
        restored = self.restored.get(payload["symbol"], payload["lookback"])
        return {"total_count": restored, "rebuilt": True}


def test_repair_dataset_bars_rebuilds_everything_it_can() -> None:
    database = StubRepairDatabase()
    database.fail_symbols = {"GC=F"}

    report = repair_dataset_bars(database)

    assert report["status"] == "partial"
    assert report["repaired"] == 1
    assert report["skipped"] == 1
    assert report["failed"] == 1
    assert [call["symbol"] for call in database.calls] == ["600105", "GC=F"]
    assert "session_start" not in database.calls[0]
    # bar_count 缺失的数据集按 lookback_floor 回源。
    assert database.calls[1]["lookback"] == 250
    assert report["items"][1]["status"] == "skipped"
    assert "local" in report["items"][1]["reason"]


def test_repair_dataset_bars_marks_short_rebuilds_as_partial() -> None:
    database = StubRepairDatabase()
    # 公开接口的分钟级历史上限很低：1000 根的数据集回源只能拿回 319 根。
    database.restored = {"600105": 319}

    report = repair_dataset_bars(database, symbols=["600105"])

    assert report["status"] == "partial"
    assert report["partial"] == 1
    assert report["repaired"] == 0
    assert report["items"][0]["stored_bar_count"] == 1000
    assert report["items"][0]["bar_count"] == 319


def test_repair_dataset_bars_clamps_lookback_floor() -> None:
    database = StubRepairDatabase()

    repair_dataset_bars(database, symbols=["GC=F"], lookback_floor=5)

    # 远端接口要求 lookback 在 10..5000，不能直接透传过小的 floor。
    assert database.calls[0]["lookback"] == 10


def test_repair_dataset_bars_reports_total_failure() -> None:
    database = StubRepairDatabase()
    database.fail_symbols = {"600105", "GC=F"}

    report = repair_dataset_bars(database, symbols=["600105", "GC=F"])

    assert report["status"] == "failed"
    assert report["repaired"] == 0
    assert report["failed"] == 2


def test_repair_dataset_bars_dry_run_does_not_sync() -> None:
    database = StubRepairDatabase()

    report = repair_dataset_bars(database, symbols=["600105"], dry_run=True)

    assert database.calls == []
    assert report["planned"] == 1
    assert report["repaired"] == 0
    assert report["items"][0]["status"] == "planned"
    assert report["items"][0]["payload"]["symbol"] == "600105"

"""行情查询必须带时间谓词，并在撞上 parquet 文件数上限时按时间二分重试。

根因：``_fetch_dataset_bars`` 的查询没有时间条件，InfluxDB 3 Core 无法按时间裁剪
parquet 文件，只能全表扫描，最终以 ``--query-file-limit`` 超限（HTTP 500）失败，
前端只能看到“行情存储不可用”。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from xquant.registry.database import Database, _dataset_time_bounds
from xquant.storage.influxdb import InfluxDBQueryError, InfluxDBStore, is_file_limit_error
from xquant.storage.settings import InfluxSettings

_FILE_LIMIT_BODY = (
    "Query would scan 4096 Parquet files, exceeding the file limit. "
    "Use a narrower time range, or increase the limit with --query-file-limit"
)

_TIME_PARAM_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_time_param(value: str) -> datetime:
    return datetime.strptime(value, _TIME_PARAM_FORMAT).replace(tzinfo=UTC)


def _status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://influx:8181/api/v3/query_sql")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError("upstream error", request=request, response=response)


class _Response:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self._rows


class _WindowClient:
    """按时间窗宽度模拟 InfluxDB：窗口过宽就报 parquet 文件数超限。"""

    def __init__(self, limit: timedelta, error_body: str = _FILE_LIMIT_BODY) -> None:
        self.limit = limit
        self.error_body = error_body
        self.windows: list[tuple[datetime, datetime]] = []
        self.params: list[dict[str, Any]] = []

    def post(self, _path: str, **kwargs: Any) -> _Response:
        params = kwargs["json"]["params"]
        self.params.append(params)
        lower = _parse_time_param(params["time_lower"])
        upper = _parse_time_param(params["time_upper"])
        self.windows.append((lower, upper))
        if upper - lower > self.limit:
            raise _status_error(500, self.error_body)
        return _Response([{"session_id": f"{lower.date().isoformat()}", "source_seq": 0}])

    def close(self) -> None:
        return None


def _store(client: Any) -> InfluxDBStore:
    return InfluxDBStore(
        InfluxSettings(
            url="http://influx:8181", database="quant-sentinel", token=SecretStr("secret")
        ),
        client=client,
    )


def test_is_file_limit_error_matches_only_the_file_limit_failure() -> None:
    assert is_file_limit_error(InfluxDBQueryError("query", _FILE_LIMIT_BODY, status_code=500))
    assert is_file_limit_error(
        InfluxDBQueryError("query", "error while planning query: file limit of 432", status_code=500)
    )
    assert not is_file_limit_error(InfluxDBQueryError("query", "syntax error at line 1"))


def test_query_time_windows_sends_rfc3339_bounds() -> None:
    client = _WindowClient(limit=timedelta(days=30))
    rows = _store(client).query_time_windows(
        "SELECT 1 AS value WHERE time >= $time_lower AND time <= $time_upper",
        {"dataset_id": "dataset-1"},
        lower=datetime(2026, 1, 1, tzinfo=UTC),
        upper=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert rows == [{"session_id": "2026-01-01", "source_seq": 0}]
    assert client.params == [
        {
            "dataset_id": "dataset-1",
            "time_lower": "2026-01-01T00:00:00Z",
            "time_upper": "2026-01-02T00:00:00Z",
        }
    ]


def test_query_time_windows_splits_the_range_on_file_limit() -> None:
    client = _WindowClient(limit=timedelta(days=1))

    rows = _store(client).query_time_windows(
        "SELECT 1 AS value WHERE time >= $time_lower AND time <= $time_upper",
        {"dataset_id": "dataset-1"},
        lower=datetime(2026, 1, 1, tzinfo=UTC),
        upper=datetime(2026, 1, 5, tzinfo=UTC),
    )

    # 4 天 -> 2 天（超限）-> 1 天（成功），共 7 次查询，其中 4 个窗口成功。
    assert len(client.windows) == 7
    assert len(rows) == 4
    assert {row["session_id"] for row in rows} == {
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
    }


def test_query_time_windows_keeps_the_dataset_param_in_every_window() -> None:
    client = _WindowClient(limit=timedelta(days=1))

    _store(client).query_time_windows(
        "SELECT 1 AS value WHERE dataset_id = $dataset_id",
        {"dataset_id": "dataset-1"},
        lower=datetime(2026, 1, 1, tzinfo=UTC),
        upper=datetime(2026, 1, 3, tzinfo=UTC),
    )

    assert {param["dataset_id"] for param in client.params} == {"dataset-1"}


def test_query_time_windows_does_not_split_other_failures() -> None:
    client = _WindowClient(limit=timedelta(days=1), error_body="syntax error at line 1")

    with pytest.raises(InfluxDBQueryError) as excinfo:
        _store(client).query_time_windows(
            "SELECT 1 AS value WHERE time >= $time_lower AND time <= $time_upper",
            {"dataset_id": "dataset-1"},
            lower=datetime(2026, 1, 1, tzinfo=UTC),
            upper=datetime(2026, 1, 5, tzinfo=UTC),
        )

    assert len(client.windows) == 1
    assert excinfo.value.detail == "syntax error at line 1"


def test_query_time_windows_reports_actionable_error_when_splitting_is_exhausted() -> None:
    client = _WindowClient(limit=timedelta(0))

    with pytest.raises(InfluxDBQueryError) as excinfo:
        _store(client).query_time_windows(
            "SELECT 1 AS value WHERE time >= $time_lower AND time <= $time_upper",
            {"dataset_id": "dataset-1"},
            lower=datetime(2026, 1, 1, tzinfo=UTC),
            upper=datetime(2026, 1, 5, tzinfo=UTC),
            max_depth=1,
        )

    # 根窗口 + 第一个子窗口就收窄到上限了，直接失败，不再继续尝试。
    assert len(client.windows) == 2
    assert "--query-file-limit" in excinfo.value.detail
    assert excinfo.value.status_code == 500


def test_dataset_time_bounds_covers_sessions_and_created_at() -> None:
    bounds = _dataset_time_bounds(
        {
            "first_session": "202608251000",
            "last_session": "202608251100",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "last_synced_at": datetime(2026, 9, 1, tzinfo=UTC),
        }
    )

    assert bounds is not None
    lower, upper = bounds
    assert lower <= datetime(2026, 1, 1, tzinfo=UTC)
    assert upper >= datetime(2026, 9, 1, tzinfo=UTC)


def test_dataset_time_bounds_falls_back_to_created_at_for_unparsable_sessions() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    bounds = _dataset_time_bounds(
        {
            "first_session": "session-a",
            "last_session": "session-b",
            "created_at": created_at,
        }
    )

    assert bounds is not None
    lower, upper = bounds
    assert lower <= created_at <= upper


def test_dataset_time_bounds_is_none_without_usable_metadata() -> None:
    assert _dataset_time_bounds(None) is None
    assert _dataset_time_bounds({}) is None
    assert _dataset_time_bounds({"first_session": "session-a", "created_at": None}) is None


_BAR_DAYS = [datetime(2026, 1, day, tzinfo=UTC) for day in range(1, 5)]


class _BarWindowClient:
    """像真 InfluxDB 一样：窗口过宽就报文件数超限，否则返回落在窗口内的 bar。"""

    def __init__(self, limit: timedelta) -> None:
        self.limit = limit
        self.windows: list[tuple[datetime, datetime]] = []

    def post(self, _path: str, **kwargs: Any) -> _Response:
        params = kwargs["json"]["params"]
        lower = _parse_time_param(params["time_lower"])
        upper = _parse_time_param(params["time_upper"])
        self.windows.append((lower, upper))
        if upper - lower > self.limit:
            raise _status_error(500, _FILE_LIMIT_BODY)
        return _Response(
            [
                {
                    "session_id": bar.date().isoformat(),
                    "source_seq": 0,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1_000.0,
                }
                for bar in _BAR_DAYS
                if lower <= bar <= upper
            ]
        )

    def close(self) -> None:
        return None


def test_fetch_dataset_bars_survives_the_file_limit_end_to_end() -> None:
    """全表扫描超限时，逐窗取回的数据必须和一次查完的结果一致。"""
    influx = InfluxDBStore(
        InfluxSettings(
            url="http://influx:8181", database="quant-sentinel", token=SecretStr("secret")
        ),
        client=_BarWindowClient(limit=timedelta(days=1)),
    )
    database = Database.__new__(Database)
    database.influx = influx  # type: ignore[assignment]

    bars = database._fetch_dataset_bars(
        "dataset-1",
        {
            "first_session": "2026-01-01",
            "last_session": "2026-01-04",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
    )

    assert [bar["session_id"] for bar in bars] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
    ]
    assert bars[0]["close"] == 100.5


class _RecordingInflux:
    def __init__(self) -> None:
        self.windowed: list[tuple[str, dict[str, Any], datetime, datetime]] = []
        self.plain: list[tuple[str, dict[str, Any]]] = []

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.plain.append((sql, dict(params or {})))
        return []

    def query_time_windows(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        lower: datetime,
        upper: datetime,
    ) -> list[dict[str, Any]]:
        self.windowed.append((sql, dict(params or {}), lower, upper))
        return []


def _database_with(influx: _RecordingInflux) -> Database:
    database = Database.__new__(Database)
    database.influx = influx  # type: ignore[assignment]
    return database


def test_fetch_dataset_bars_uses_a_time_bounded_query() -> None:
    influx = _RecordingInflux()
    database = _database_with(influx)

    database._fetch_dataset_bars(
        "dataset-1",
        {
            "first_session": "202608251000",
            "last_session": "202608251100",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
    )

    assert influx.plain == []
    assert len(influx.windowed) == 1
    sql, params, lower, _upper = influx.windowed[0]
    assert "time >= $time_lower" in sql
    assert "time <= $time_upper" in sql
    assert params == {"dataset_id": "dataset-1"}
    assert lower <= datetime(2026, 1, 1, tzinfo=UTC)


def test_fetch_dataset_bars_falls_back_to_the_unbounded_query_without_metadata() -> None:
    influx = _RecordingInflux()
    database = _database_with(influx)

    database._fetch_dataset_bars("dataset-1", None)

    assert influx.windowed == []
    assert len(influx.plain) == 1
    sql, params = influx.plain[0]
    assert params == {"dataset_id": "dataset-1"}
    assert "time >= $time_lower" not in sql

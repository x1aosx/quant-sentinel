from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from .settings import InfluxSettings

# 上游错误正文只保留前若干字符，避免把整个响应体塞进接口详情。
_MAX_ERROR_DETAIL_CHARS = 500

# InfluxDB 3 Core 用 ``--query-file-limit`` 限制单条查询能打开的 parquet 文件数，
# 超限时直接以 HTTP 500 失败。命中该错误可以收窄时间范围重试。
_FILE_LIMIT_MARKERS = ("file limit", "query-file-limit")

# InfluxDB 3 SQL 的时间边界写法：RFC3339 UTC 字符串。
_TIME_PARAM_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# 时间二分重试的上限：最多 2**8 个窗口，且单个窗口不小于 1 小时。
# 只有失败的窗口会继续拆分，所以常见情况下只会多出一两次查询；
# 深度上限只用于兜底（例如文件高度集中在近期，需要收得很窄）。
DEFAULT_MAX_TIME_SPLIT_DEPTH = 8
DEFAULT_MIN_TIME_WINDOW = timedelta(hours=1)


def is_file_limit_error(exc: BaseException) -> bool:
    """判断异常是否为 InfluxDB 3 Core 的 parquet 文件数超限。"""
    detail = str(getattr(exc, "detail", "") or exc).lower()
    return any(marker in detail for marker in _FILE_LIMIT_MARKERS)


def _format_time_param(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime(_TIME_PARAM_FORMAT)


class InfluxDBQueryError(RuntimeError):
    """InfluxDB 的 HTTP 接口返回错误或不可达时抛出。

    存储层不再把 ``httpx`` 的异常原样抛给调用方，上层可以据此返回
    「依赖不可用」而不是一个无法解释的裸 500。
    """

    def __init__(
        self,
        operation: str,
        detail: str,
        *,
        status_code: int | None = None,
    ) -> None:
        self.operation = operation
        self.detail = detail
        self.status_code = status_code
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        message = f"InfluxDB {operation} failed{suffix}"
        super().__init__(f"{message}: {detail}" if detail else message)


def _error_detail(response: httpx.Response) -> str:
    try:
        text = response.text
    except (httpx.ResponseNotRead, UnicodeDecodeError):
        # 读取上游正文失败不应掩盖真正的故障，退化为空详情。
        return ""
    return " ".join(text.split())[:_MAX_ERROR_DETAIL_CHARS]


def _request_error(operation: str, exc: httpx.RequestError) -> InfluxDBQueryError:
    return InfluxDBQueryError(operation, f"{type(exc).__name__}: {exc}")


class InfluxDBStore:
    def __init__(self, settings: InfluxSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.Client(
            base_url=settings.url,
            timeout=settings.timeout_seconds,
            headers={"Authorization": settings.auth_header},
        )

    def write_points(self, points: list[dict[str, Any]]) -> int:
        lines = [self.point_to_line_protocol(point) for point in points]
        return self.write_line_protocol(lines)

    def write_line_protocol(self, lines: list[str]) -> int:
        if not lines:
            return 0
        try:
            response = self._client.post(
                self.settings.write_path,
                params={
                    "db": self.settings.database,
                    "precision": "ns",
                    "accept_partial": "false",
                },
                content="\n".join(lines) + "\n",
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise InfluxDBQueryError(
                "write",
                _error_detail(exc.response),
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise _request_error("write", exc) from exc
        return len(lines)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            response = self._client.post(
                self.settings.query_path,
                json={
                    "db": self.settings.database,
                    "q": sql,
                    "params": params or {},
                    "format": "json",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise InfluxDBQueryError(
                "query",
                _error_detail(exc.response),
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise _request_error("query", exc) from exc
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("InfluxDB query response must be a JSON array")
        return [dict(row) for row in payload]

    def query_time_windows(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        lower: datetime,
        upper: datetime,
        max_depth: int = DEFAULT_MAX_TIME_SPLIT_DEPTH,
        min_window: timedelta = DEFAULT_MIN_TIME_WINDOW,
    ) -> list[dict[str, Any]]:
        """带时间边界的查询；撞上 parquet 文件数上限时自动二分时间范围重试。

        ``sql`` 通过 ``$time_lower`` / ``$time_upper`` 引用时间边界。InfluxDB 3
        依据时间谓词裁剪 parquet 文件，所以把一次全表扫描拆成若干时间窗后，每个
        窗口实际打开的文件数会明显下降，从而绕开 ``--query-file-limit``。

        相邻窗口在边界上都是闭区间，边界处的行可能被返回两次，由调用方按
        session_id 去重（``dedupe_sorted_bars``）。宁可重复也不丢数据。
        """
        return self._query_time_windows(
            sql,
            dict(params or {}),
            lower,
            upper,
            depth=0,
            max_depth=max_depth,
            min_window=min_window,
        )

    def _query_time_windows(
        self,
        sql: str,
        base_params: dict[str, Any],
        lower: datetime,
        upper: datetime,
        *,
        depth: int,
        max_depth: int,
        min_window: timedelta,
    ) -> list[dict[str, Any]]:
        window_params = {
            **base_params,
            "time_lower": _format_time_param(lower),
            "time_upper": _format_time_param(upper),
        }
        try:
            return self.query(sql, window_params)
        except InfluxDBQueryError as exc:
            if not is_file_limit_error(exc):
                raise
            span = upper - lower
            if depth >= max_depth or span <= min_window:
                raise InfluxDBQueryError(
                    "query",
                    (
                        f"{exc.detail}；时间范围已收窄到 "
                        f"{_format_time_param(lower)} ~ {_format_time_param(upper)} "
                        "仍然超过文件数上限，请在 InfluxDB 侧提高 --query-file-limit"
                    ),
                    status_code=exc.status_code,
                ) from exc
            middle = lower + span / 2
            return [
                *self._query_time_windows(
                    sql,
                    base_params,
                    lower,
                    middle,
                    depth=depth + 1,
                    max_depth=max_depth,
                    min_window=min_window,
                ),
                *self._query_time_windows(
                    sql,
                    base_params,
                    middle,
                    upper,
                    depth=depth + 1,
                    max_depth=max_depth,
                    min_window=min_window,
                ),
            ]

    def health(self) -> dict[str, str]:
        try:
            response = self._client.get("/health")
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise InfluxDBQueryError(
                "health",
                _error_detail(exc.response),
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise _request_error("health", exc) from exc
        return {"status": "ok"}

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def point_to_line_protocol(point: dict[str, Any]) -> str:
        measurement = _escape_tag_value(str(point["measurement"]))
        tags = "".join(
            f",{_escape_tag_value(str(key))}={_escape_tag_value(str(value))}"
            for key, value in point.get("tags", {}).items()
        )
        fields = ",".join(
            f"{_escape_tag_value(str(key))}={_format_field(value)}"
            for key, value in point.get("fields", {}).items()
        )
        if not fields:
            raise ValueError("InfluxDB point must contain at least one field")
        time_ns = _to_nanoseconds(point.get("time"))
        suffix = f" {time_ns}" if time_ns is not None else ""
        return f"{measurement}{tags} {fields}{suffix}"


def _escape_tag_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _format_field(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("InfluxDB fields cannot be NaN or infinite")
        return repr(value)
    if isinstance(value, Decimal):
        return f"{value}i" if value == value.to_integral_value() else str(value)
    if isinstance(value, datetime):
        return f"{_to_nanoseconds(value)}i"
    if value is None:
        raise ValueError("InfluxDB fields cannot be null")
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _to_nanoseconds(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return int(value.timestamp() * 1_000_000_000)
    raise TypeError(f"Unsupported InfluxDB time value: {type(value).__name__}")

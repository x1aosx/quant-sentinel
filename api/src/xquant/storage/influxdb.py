from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from .settings import InfluxSettings

# 上游错误正文只保留前若干字符，避免把整个响应体塞进接口详情。
_MAX_ERROR_DETAIL_CHARS = 500


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

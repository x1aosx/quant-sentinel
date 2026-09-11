from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from .settings import InfluxSettings


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
        response = self._client.post(
            self.settings.write_path,
            params={"db": self.settings.database, "precision": "ns", "accept_partial": "false"},
            content="\n".join(lines) + "\n",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        response.raise_for_status()
        return len(lines)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("InfluxDB query response must be a JSON array")
        return [dict(row) for row in payload]

    def delete_market_bars(self, dataset_id: str) -> None:
        escaped_dataset_id = _escape_predicate_string(dataset_id)
        response = self._client.post(
            self.settings.delete_path,
            json={
                "db": self.settings.database,
                "measurement": "market_bar",
                "predicate": f"dataset_id = '{escaped_dataset_id}'",
            },
        )
        response.raise_for_status()

    def health(self) -> dict[str, str]:
        response = self._client.get("/health")
        response.raise_for_status()
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


def _escape_predicate_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


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

"""Regression tests for datasets whose bars carry no volume field.

Root cause: ``sr_levels._normalize_bars`` required ``volume`` and raised a bare
KeyError, which escaped the ``except (TypeError, ValueError)`` guards in
``build_snapshot`` and in ``/ai/analyze/stream``, so Starlette answered with a
plain-text 500 and the frontend could not read any ``detail``.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from xquant.analysis.sr_levels import _normalize_bars, detect_support_resistance
from xquant.api.app import create_app


def _bar(index: int, close: float, *, spread: float = 0.7) -> dict[str, object]:
    open_price = close - spread * 0.4
    return {
        "session_id": (date(2024, 1, 1) + timedelta(days=index)).isoformat(),
        "open": open_price,
        "high": close + spread * 0.5,
        "low": open_price - spread * 0.5,
        "close": close,
    }


def _bars_without_volume(count: int = 120) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    close = 100.0
    for index in range(count):
        close += 0.15 + (0.05 if index % 7 == 0 else -0.03)
        bars.append(_bar(index, close))
    return bars


def _insert_dataset(client: TestClient, symbol: str, bars: list[dict[str, object]]) -> str:
    summary = client.app.state.db.insert_dataset(
        {
            "symbol": symbol,
            "timeframe": "1d",
            "bars": bars,
            "source": "unit-test",
            "source_provider": "unit-test",
        }
    )
    return str(summary["id"])


def _sse_events(response) -> list[dict[str, object]]:
    return [
        json.loads(line[5:].strip())
        for chunk in response.text.split("\n\n")
        for line in chunk.splitlines()
        if line.startswith("data:")
    ]


def test_normalize_bars_defaults_missing_volume_to_zero() -> None:
    normalized = _normalize_bars([_bar(0, 100.0), _bar(1, 101.0)])

    assert [item["volume"] for item in normalized] == [0.0, 0.0]
    assert [item["close"] for item in normalized] == [100.0, 101.0]


def test_normalize_bars_keeps_explicit_volume_and_aliases() -> None:
    explicit = _normalize_bars([{**_bar(0, 100.0), "volume": 1500}])
    aliased = _normalize_bars([{**_bar(0, 100.0), "v": 1500}])
    nulled = _normalize_bars([{**_bar(0, 100.0), "volume": None}])

    assert explicit[0]["volume"] == 1500.0
    assert aliased[0]["volume"] == 1500.0
    assert nulled[0]["volume"] == 0.0


def test_normalize_bars_reports_missing_ohlc_as_value_error() -> None:
    for missing in ("open", "high", "low", "close"):
        bar = _bar(0, 100.0)
        bar.pop(missing)
        with pytest.raises(ValueError, match=f"第 1 根K线数值无效：缺少字段 {missing}"):
            _normalize_bars([bar])


def test_normalize_bars_still_rejects_invalid_numbers() -> None:
    with pytest.raises(ValueError, match="close 必须是有限数字"):
        _normalize_bars([{**_bar(0, 100.0), "close": float("nan")}])
    with pytest.raises(ValueError, match="volume 必须是有限数字"):
        _normalize_bars([{**_bar(0, 100.0), "volume": float("inf")}])
    with pytest.raises(ValueError, match="第 1 根K线的 OHLC 关系无效"):
        _normalize_bars([{**_bar(0, 100.0), "low": 200.0}])


def test_detect_support_resistance_accepts_bars_without_volume() -> None:
    bars = _bars_without_volume()
    zero_volume_bars = [{**bar, "volume": 0.0} for bar in bars]

    missing_volume = detect_support_resistance(bars, symbol="600000", timeframe="1d")
    explicit_zero = detect_support_resistance(zero_volume_bars, symbol="600000", timeframe="1d")

    # Missing volume must behave exactly like an explicit volume of 0.0.
    assert missing_volume == explicit_zero
    assert missing_volume["bars_used"] == 120
    assert missing_volume["current_price"] is not None


def test_stream_endpoint_accepts_bars_without_volume(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        dataset_id = _insert_dataset(client, "NOVOL.UNIT", _bars_without_volume())

        response = client.post("/api/v1/ai/analyze/stream", json={"dataset_id": dataset_id})

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _sse_events(response)
        assert events, response.text
        assert events[-1]["type"] == "done"
        assert events[-1]["record"]["decision_tree_layout"]
        assert not [event for event in events if event["type"] == "error"]


@pytest.mark.parametrize(
    ("mutate", "expected_detail"),
    [
        (lambda bar: bar.pop("open"), "缺少字段 open"),
        (lambda bar: bar.update({"high": 10.0}), "OHLC 关系无效"),
        (lambda bar: bar.update({"close": {"value": 1}}), "close 必须是数字"),
    ],
)
def test_stream_endpoint_reports_invalid_bars_as_json_400(
    tmp_path, mutate, expected_detail: str
) -> None:
    with TestClient(create_app(tmp_path / "quant.db"), raise_server_exceptions=False) as client:
        bars = _bars_without_volume()
        # Mutate the newest bar so it lands inside the analysed window.
        mutate(bars[-1])
        dataset_id = _insert_dataset(client, "BAD.UNIT", bars)

        response = client.post("/api/v1/ai/analyze/stream", json={"dataset_id": dataset_id})

        assert response.status_code == 400, response.text
        assert response.headers["content-type"].startswith("application/json")
        detail = response.json()["detail"]
        assert expected_detail in detail
        assert "根K线" in detail


def test_stream_endpoint_reports_unexpected_failure_with_real_detail(
    tmp_path, monkeypatch
) -> None:
    """Unanticipated errors must surface as a JSON 500 carrying the real message."""
    with TestClient(create_app(tmp_path / "quant.db"), raise_server_exceptions=False) as client:
        dataset_id = _insert_dataset(client, "BOOM.UNIT", _bars_without_volume())

        def boom(**kwargs):
            raise RuntimeError("快照构建内部错误")

        monkeypatch.setattr("xquant.api.routes.ai.build_snapshot", boom)

        response = client.post("/api/v1/ai/analyze/stream", json={"dataset_id": dataset_id})

        assert response.status_code == 500, response.text
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["detail"] == "快照构建内部错误"


def test_analyze_endpoint_accepts_bars_without_volume(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        dataset_id = _insert_dataset(client, "NONSTREAM.UNIT", _bars_without_volume())

        response = client.post("/api/v1/ai/analyze", json={"dataset_id": dataset_id})

        assert response.status_code == 200, response.text
        assert response.json()["decision_tree_layout"]


def test_analyze_endpoint_reports_invalid_bars_as_json_400(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "quant.db"), raise_server_exceptions=False) as client:
        bars = _bars_without_volume()
        # Mutate the newest bar so it lands inside the analysed window.
        bars[-1].pop("open")
        dataset_id = _insert_dataset(client, "BADNONSTREAM.UNIT", bars)

        response = client.post("/api/v1/ai/analyze", json={"dataset_id": dataset_id})

        assert response.status_code == 400, response.text
        assert "缺少字段 open" in response.json()["detail"]


def test_insert_dataset_bars_round_trip_has_no_volume(tmp_path) -> None:
    """Guard the fixture: the persisted dataset really has no volume field."""
    with TestClient(create_app(tmp_path / "quant.db")) as client:
        dataset_id = _insert_dataset(client, "SHAPE.UNIT", _bars_without_volume())

        stored = client.app.state.db.get_dataset(dataset_id)

        assert stored["bars"]
        assert all("volume" not in bar for bar in stored["bars"])

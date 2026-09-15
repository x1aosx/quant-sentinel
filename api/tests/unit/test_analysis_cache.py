from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from xquant.api.app import create_app
from xquant.api.routes import analysis as analysis_routes
from xquant.registry import Database
from xquant.registry.sqlite import Database as LegacySqliteDatabase


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
                client, symbol="BBB", title="Beta", timeframe="4h"
            ),
            "CCC": _create_dataset(
                client, symbol="CCC", title="Gamma", timeframe="1d"
            ),
        }

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
            "timeframes": ["1d", "4h"],
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

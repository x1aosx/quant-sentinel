"""候选池扩展 API 契约测试（扫描、持久化、刷新联动）。"""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.discovery import (
    ORIGIN_MARKET_RANKING,
    EastmoneyRankingSource,
    SinaRankingSource,
    UniverseLead,
)


def _patch_ranking(monkeypatch: Any) -> None:
    def fake_discover(self: EastmoneyRankingSource) -> list[UniverseLead]:
        return [
            UniverseLead(
                symbol="600519",
                name="贵州茅台",
                origin=ORIGIN_MARKET_RANKING,
                reasons=["涨幅榜第 1 名"],
                signals={"rank_score": 88.0},
            )
        ]

    monkeypatch.setattr(EastmoneyRankingSource, "discover", fake_discover)
    monkeypatch.setattr(SinaRankingSource, "discover", lambda self: [])


def test_universe_scan_endpoint_persists_report(tmp_path, monkeypatch) -> None:
    _patch_ranking(monkeypatch)
    app = create_app(tmp_path / "xquant.db")

    with TestClient(app) as client:
        initial = client.get("/api/v1/discovery/universe")
        assert initial.status_code == 200
        assert initial.json() == {"enabled": True, "latest": None}

        scanned = client.post("/api/v1/discovery/universe/scan", json={"sync": False})
        assert scanned.status_code == 200, scanned.text
        payload = scanned.json()
        assert payload["candidate_count"] == 1
        assert payload["synced_count"] == 0
        candidate = payload["candidates"][0]
        assert candidate["symbol"] == "600519"
        assert candidate["name"] == "贵州茅台"
        assert candidate["status"] == "PENDING"
        assert candidate["origin"] == ORIGIN_MARKET_RANKING

        restored = client.get("/api/v1/discovery/universe")
        assert restored.status_code == 200
        latest = restored.json()["latest"]
        assert latest["scanned_at"] == payload["scanned_at"]
        assert latest["candidates"][0]["symbol"] == "600519"
        assert latest["origin_counts"] == {ORIGIN_MARKET_RANKING: 1}


def test_refresh_with_expand_universe_syncs_and_ranks(tmp_path, monkeypatch) -> None:
    _patch_ranking(monkeypatch)
    app = create_app(tmp_path / "xquant.db")

    with TestClient(app) as client:
        created = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
        assert created.status_code == 200, created.text

        fastapi_app = cast(FastAPI, app)
        scanner = fastapi_app.state.discovery_service._universe_scanner
        scanner._sync = lambda symbol: {
            "id": f"dataset-{symbol}",
            "symbol": symbol,
            "title": "贵州茅台",
        }

        refreshed = client.post("/api/v1/discovery/refresh?expand_universe=true")
        assert refreshed.status_code == 200, refreshed.text
        payload = refreshed.json()
        assert payload["universe"]["synced_count"] == 1
        assert payload["universe"]["candidate_count"] == 1
        assert payload["count"] >= 1

        latest = client.get("/api/v1/discovery/universe").json()["latest"]
        assert latest["synced_count"] == 1
        assert latest["candidates"][0]["status"] == "SYNCED"


def test_universe_endpoints_fail_closed_without_runtime(tmp_path) -> None:
    app = create_app(tmp_path / "xquant.db")
    app.state.discovery_service = None
    with TestClient(app) as client:
        response = client.get("/api/v1/discovery/universe")
        assert response.status_code == 503


def test_universe_scanner_stays_wired_to_runtime(tmp_path) -> None:
    app = create_app(tmp_path / "xquant.db")
    scanner = app.state.discovery_service._universe_scanner
    assert callable(scanner.scan)
    assert [source.name for source in scanner.sources] == [
        "sina_ranking",
        "eastmoney_ranking",
    ]

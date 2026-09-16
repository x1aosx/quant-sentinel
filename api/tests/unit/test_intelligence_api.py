from __future__ import annotations

from fastapi.testclient import TestClient

from xquant.api.app import create_app


def test_intelligence_and_discovery_api_flow(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        overview = client.get("/api/v1/intelligence/overview")
        assert overview.status_code == 200
        assert overview.json()["information_count"] == 0
        assert overview.json()["source_count"] == 0

        run = client.post("/api/v1/intelligence/run", json={"process_only": True})
        assert run.status_code == 200, run.text
        assert run.json()["collected"] == 0
        assert run.json()["event_count"] == 0

        created = client.post("/api/v1/datasets/sample", json={"timeframe": "1d"})
        assert created.status_code == 200, created.text

        refreshed = client.post("/api/v1/discovery/refresh")
        assert refreshed.status_code == 200, refreshed.text
        payload = refreshed.json()
        assert payload["count"] == 1
        candidate = payload["items"][0]
        assert candidate["stock_id"] == "DEMO.RESEARCH"
        assert candidate["discovery_score"] >= 0
        assert candidate["score_contributions"]
        assert candidate["model_version"] == "discovery-mvp-v1"

        listed = client.get("/api/v1/discovery/candidates")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["items"][0]["reasons"]


def test_intelligence_sources_reload_after_system_config_update(tmp_path) -> None:
    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        config = client.get("/api/v1/system/config").json()["config"]
        config["intelligence"]["feeds"] = [
            {
                "id": "policy-feed",
                "url": "https://example.com/policy.xml",
                "source": "政策发布",
                "source_type": "POLICY",
                "language": "zh-CN",
                "enabled": True,
            }
        ]

        saved = client.put("/api/v1/system/config", json=config)
        assert saved.status_code == 200, saved.text

        overview = client.get("/api/v1/intelligence/overview")
        assert overview.status_code == 200
        assert overview.json()["source_count"] == 1

        sources = client.get("/api/v1/intelligence/sources")
        assert sources.status_code == 200
        assert sources.json()["items"][0]["source"] == "政策发布"
        assert sources.json()["items"][0]["source_type"] == "POLICY"

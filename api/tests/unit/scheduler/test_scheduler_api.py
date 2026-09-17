from __future__ import annotations

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.storage import SchedulerSettings, StorageSettings


def test_scheduler_api_is_available_when_enabled(tmp_path) -> None:
    settings = StorageSettings(
        storage_backend="legacy_sqlite",
        scheduler=SchedulerSettings(
            enabled=True,
            embedded=True,
            engine_type="memory",
            dispatcher_type="local",
        ),
    )

    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            settings=settings,
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        tasks = client.get("/api/v1/scheduler/tasks")
        assert tasks.status_code == 200
        assert {item["name"] for item in tasks.json()["items"]} == {
            "market.daily.sync",
            "market.symbol.sync",
            "market.watchlist.summary",
            "intelligence.collect",
            "intelligence.process",
            "intelligence.brief.morning",
            "alpha.training.run",
            "alpha.backtest.run",
            "alpha.realtime.evaluate",
        }

        created = client.post(
            "/api/v1/scheduler/schedules",
            json={
                "id": "daily-test",
                "task_name": "market.daily.sync",
                "trigger": {"type": "interval", "interval_seconds": 60},
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["trigger"]["type"] == "interval"

        listed = client.get("/api/v1/scheduler/schedules")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == ["daily-test"]

        paused = client.post("/api/v1/scheduler/schedules/daily-test/pause")
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False


def test_scheduler_api_is_unavailable_when_disabled(tmp_path) -> None:
    with TestClient(create_app(tmp_path / "xquant.db")) as client:
        response = client.get("/api/v1/scheduler/tasks")

    assert response.status_code == 503
    assert response.json()["detail"] == "定时任务模块未启用"


def test_schedule_changes_do_not_require_embedded_engine(tmp_path) -> None:
    settings = StorageSettings(
        storage_backend="legacy_sqlite",
        scheduler=SchedulerSettings(
            enabled=True,
            embedded=False,
            engine_type="memory",
            dispatcher_type="redis",
        ),
    )

    with TestClient(
        create_app(
            tmp_path / "xquant.db",
            settings=settings,
            system_config_path=tmp_path / "system-settings.json",
        )
    ) as client:
        response = client.post(
            "/api/v1/scheduler/schedules",
            json={
                "id": "external-engine",
                "task_name": "market.daily.sync",
                "trigger": {"type": "interval", "interval_seconds": 60},
            },
        )

    assert response.status_code == 200, response.text

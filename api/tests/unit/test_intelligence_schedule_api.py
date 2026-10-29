from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.intelligence.schedules import INTELLIGENCE_ORIGIN
from xquant.storage import SchedulerSettings, StorageSettings


def _settings(*, scheduler_enabled: bool = True) -> StorageSettings:
    return StorageSettings(
        storage_backend="legacy_sqlite",
        scheduler=SchedulerSettings(
            enabled=scheduler_enabled,
            embedded=True,
            engine_type="memory",
            dispatcher_type="local",
        ),
    )


def _client(tmp_path, *, scheduler_enabled: bool = True) -> TestClient:
    return TestClient(
        create_app(
            tmp_path / "xquant.db",
            settings=_settings(scheduler_enabled=scheduler_enabled),
            system_config_path=tmp_path / "system-settings.json",
        )
    )


def _intelligence_schedule(client: TestClient) -> dict[str, Any] | None:
    response = client.get("/api/v1/scheduler/schedules")
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["params"].get("origin") == INTELLIGENCE_ORIGIN:
            return item
    return None


def test_schedule_status_defaults_to_disabled(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/intelligence/schedule")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "schedule_id": None,
            "task_name": "intelligence.run",
            "enabled": False,
            "interval_seconds": None,
            "timezone": "Asia/Shanghai",
            "next_fire_at": None,
        }


def test_enable_creates_single_schedule_with_default_interval(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.put("/api/v1/intelligence/schedule", json={"enabled": True})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True
        assert body["task_name"] == "intelligence.run"
        assert body["interval_seconds"] == 1800
        assert body["next_fire_at"] is not None

        schedule = _intelligence_schedule(client)
        assert schedule is not None
        assert schedule["id"] == body["schedule_id"]

        # 重复启用不会产生第二条计划。
        again = client.put(
            "/api/v1/intelligence/schedule",
            json={"enabled": True, "interval_seconds": 3600},
        )
        assert again.status_code == 200, again.text
        assert again.json()["schedule_id"] == body["schedule_id"]
        assert again.json()["interval_seconds"] == 3600
        assert (
            len(
                [
                    item
                    for item in client.get("/api/v1/scheduler/schedules").json()["items"]
                    if item["params"].get("origin") == INTELLIGENCE_ORIGIN
                ]
            )
            == 1
        )


def test_disable_keeps_schedule_but_marks_disabled(tmp_path) -> None:
    with _client(tmp_path) as client:
        created = client.put("/api/v1/intelligence/schedule", json={"enabled": True})
        assert created.status_code == 200, created.text
        schedule_id = created.json()["schedule_id"]

        disabled = client.put("/api/v1/intelligence/schedule", json={"enabled": False})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False
        assert disabled.json()["schedule_id"] == schedule_id
        assert disabled.json()["next_fire_at"] is None

        status = client.get("/api/v1/intelligence/schedule")
        assert status.status_code == 200, status.text
        assert status.json()["enabled"] is False


def test_invalid_interval_is_rejected(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.put(
            "/api/v1/intelligence/schedule",
            json={"enabled": True, "interval_seconds": 10},
        )
        assert response.status_code == 400, response.text
        assert "300" in response.json()["detail"]

        text = client.put(
            "/api/v1/intelligence/schedule",
            json={"enabled": True, "interval_seconds": "abc"},
        )
        assert text.status_code == 400, text.text


def test_schedule_unavailable_when_scheduler_disabled(tmp_path) -> None:
    with _client(tmp_path, scheduler_enabled=False) as client:
        status = client.get("/api/v1/intelligence/schedule")
        assert status.status_code == 503, status.text

        update = client.put("/api/v1/intelligence/schedule", json={"enabled": True})
        assert update.status_code == 503, update.text

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.marketdata.sync_policy import DATASET_SYNC_ORIGIN
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


def _bars(count: int = 120) -> list[dict[str, Any]]:
    return [
        {
            "session_id": f"20260101{index:04d}",
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1_000.0,
        }
        for index in range(count)
    ]


def _insert_dataset(
    client: TestClient,
    symbol: str,
    timeframe: str,
    *,
    source: str = "yfinance",
) -> str:
    summary = client.app.state.db.insert_dataset(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": _bars(),
            "source": source,
            "source_provider": "yahoo",
        }
    )
    return str(summary["id"])


def _schedules(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/v1/scheduler/schedules")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _dataset_schedules(client: TestClient) -> dict[str, dict[str, Any]]:
    return {
        str(item["params"]["dataset_id"]): item
        for item in _schedules(client)
        if item["params"].get("origin") == DATASET_SYNC_ORIGIN
        and item["params"].get("dataset_id")
    }


def test_enable_dataset_sync_defaults_interval_to_timeframe(tmp_path) -> None:
    with _client(tmp_path) as client:
        dataset_id = _insert_dataset(client, "AAPL", "15m")

        response = client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": True}
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["dataset_id"] == dataset_id
        assert payload["enabled"] is True
        assert payload["interval_seconds"] == 900
        assert payload["task_name"] == "market.daily.sync"

        schedule = _dataset_schedules(client)[dataset_id]
        assert schedule["trigger"]["type"] == "interval"
        assert schedule["trigger"]["interval_seconds"] == 900
        assert schedule["params"]["timeframe"] == "15m"
        assert schedule["params"]["symbol"] == "AAPL"


def test_interval_denser_than_timeframe_is_rejected(tmp_path) -> None:
    with _client(tmp_path) as client:
        dataset_id = _insert_dataset(client, "AAPL", "15m")

        response = client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule",
            json={"enabled": True, "interval_seconds": 60},
        )

        assert response.status_code == 400
        assert "900" in response.json()["detail"]
        assert _dataset_schedules(client) == {}


def test_disable_keeps_schedule_but_marks_disabled(tmp_path) -> None:
    with _client(tmp_path) as client:
        dataset_id = _insert_dataset(client, "AAPL", "15m")
        client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": True}
        )

        response = client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": False}
        )

        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is False
        assert _dataset_schedules(client)[dataset_id]["enabled"] is False


def test_enabling_one_timeframe_adopts_sibling_timeframes(tmp_path) -> None:
    with _client(tmp_path) as client:
        daily_id = _insert_dataset(client, "AAPL", "1d")
        intraday_id = _insert_dataset(client, "AAPL", "15m")

        response = client.put(
            f"/api/v1/datasets/{daily_id}/sync-schedule", json={"enabled": True}
        )

        assert response.status_code == 200, response.text
        adopted = _dataset_schedules(client)
        assert set(adopted) == {daily_id, intraday_id}
        assert adopted[intraday_id]["enabled"] is True
        assert adopted[intraday_id]["trigger"]["interval_seconds"] == 900


def test_disabling_one_timeframe_does_not_reenable_it(tmp_path) -> None:
    with _client(tmp_path) as client:
        daily_id = _insert_dataset(client, "AAPL", "1d")
        intraday_id = _insert_dataset(client, "AAPL", "15m")
        client.put(
            f"/api/v1/datasets/{daily_id}/sync-schedule", json={"enabled": True}
        )

        response = client.put(
            f"/api/v1/datasets/{intraday_id}/sync-schedule", json={"enabled": False}
        )

        assert response.status_code == 200, response.text
        adopted = _dataset_schedules(client)
        assert adopted[daily_id]["enabled"] is True
        assert adopted[intraday_id]["enabled"] is False


def test_local_dataset_cannot_enable_sync(tmp_path) -> None:
    with _client(tmp_path) as client:
        dataset_id = _insert_dataset(client, "LOCAL.RESEARCH", "1d", source="local")

        response = client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": True}
        )

        assert response.status_code == 400
        assert "本地文件" in response.json()["detail"]


def test_reconcile_adopts_new_timeframe_for_enabled_symbol(tmp_path) -> None:
    with _client(tmp_path) as client:
        daily_id = _insert_dataset(client, "AAPL", "1d")
        client.put(
            f"/api/v1/datasets/{daily_id}/sync-schedule", json={"enabled": True}
        )
        intraday_id = _insert_dataset(client, "AAPL", "15m")
        assert intraday_id not in _dataset_schedules(client)

        response = client.post("/api/v1/datasets/sync-schedules/reconcile")

        assert response.status_code == 200, response.text
        assert response.json()["created"] == [intraday_id]
        adopted = _dataset_schedules(client)[intraday_id]
        assert adopted["enabled"] is True
        assert adopted["trigger"]["interval_seconds"] == 900
        assert adopted["params"]["timeframe"] == "15m"


def test_reconcile_splits_legacy_symbol_schedule(tmp_path) -> None:
    with _client(tmp_path) as client:
        daily_id = _insert_dataset(client, "AAPL", "1d")
        intraday_id = _insert_dataset(client, "AAPL", "15m")
        created = client.post(
            "/api/v1/scheduler/schedules",
            json={
                "id": "legacy-aapl",
                "task_name": "market.symbol.sync",
                "trigger": {"type": "interval", "interval_seconds": 300},
                "params": {
                    "origin": DATASET_SYNC_ORIGIN,
                    "symbol": "AAPL",
                    "dataset_ids": [daily_id, intraday_id],
                    "symbols": [
                        {
                            "dataset_id": daily_id,
                            "source": "yfinance",
                            "symbol": "AAPL",
                            "timeframe": "1d",
                            "lookback": 500,
                            "adjust": "qfq",
                        },
                        {
                            "dataset_id": intraday_id,
                            "source": "yfinance",
                            "symbol": "AAPL",
                            "timeframe": "15m",
                            "lookback": 500,
                            "adjust": "qfq",
                        },
                    ],
                },
            },
        )
        assert created.status_code == 200, created.text

        response = client.post("/api/v1/datasets/sync-schedules/reconcile")

        assert response.status_code == 200, response.text
        assert sorted(response.json()["created"]) == sorted([daily_id, intraday_id])
        schedules = _schedules(client)
        assert [item["id"] for item in schedules if item["id"] == "legacy-aapl"] == []
        per_dataset = _dataset_schedules(client)
        assert per_dataset[daily_id]["trigger"]["interval_seconds"] == 86_400
        assert per_dataset[intraday_id]["trigger"]["interval_seconds"] == 900


def test_reconcile_removes_schedule_of_missing_dataset(tmp_path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/scheduler/schedules",
            json={
                "id": "stale-dataset",
                "task_name": "market.daily.sync",
                "trigger": {"type": "interval", "interval_seconds": 900},
                "params": {
                    "origin": DATASET_SYNC_ORIGIN,
                    "dataset_id": "does-not-exist",
                    "symbol": "AAPL",
                    "timeframe": "15m",
                    "source": "yfinance",
                },
            },
        )
        assert created.status_code == 200, created.text

        response = client.post("/api/v1/datasets/sync-schedules/reconcile")

        assert response.status_code == 200, response.text
        assert response.json()["deleted"] == ["stale-dataset"]
        assert _schedules(client) == []


def test_delete_dataset_drops_its_sync_schedule(tmp_path) -> None:
    with _client(tmp_path) as client:
        dataset_id = _insert_dataset(client, "AAPL", "15m")
        client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": True}
        )

        response = client.delete(f"/api/v1/datasets/{dataset_id}")

        assert response.status_code == 200, response.text
        assert _dataset_schedules(client) == {}


def test_stale_schedule_is_repaired_after_dataset_rename(tmp_path) -> None:
    with _client(tmp_path) as client:
        dataset_id = _insert_dataset(client, "AAPL", "15m")
        client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": True}
        )
        schedule_id = _dataset_schedules(client)[dataset_id]["id"]
        updated = client.put(
            f"/api/v1/scheduler/schedules/{schedule_id}",
            json={"params": {**_dataset_schedules(client)[dataset_id]["params"], "symbol": "STALE"}},
        )
        assert updated.status_code == 200, updated.text

        response = client.post("/api/v1/datasets/sync-schedules/reconcile")

        assert response.status_code == 200, response.text
        assert response.json()["updated"] == [dataset_id]
        assert _dataset_schedules(client)[dataset_id]["params"]["symbol"] == "AAPL"


def test_remote_sync_adopts_new_timeframe_automatically(tmp_path, monkeypatch) -> None:
    with _client(tmp_path) as client:
        daily_id = _insert_dataset(client, "AAPL", "1d")
        client.put(
            f"/api/v1/datasets/{daily_id}/sync-schedule", json={"enabled": True}
        )
        # 模拟 sync_dataset 已经把 15m 数据集写入库，随后同步接口返回成功。
        intraday_id = _insert_dataset(client, "AAPL", "15m")
        database = client.app.state.db

        def fake_sync(payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": intraday_id,
                "symbol": payload["symbol"],
                "timeframe": payload["timeframe"],
                "bar_count": 200,
                "total_count": 200,
                "inserted_count": 8,
                "updated_count": 192,
                "sync_status": "updated",
            }

        monkeypatch.setattr(database, "sync_dataset", fake_sync)

        response = client.post(
            "/api/v1/datasets/remote/sync",
            json={
                "source": "yfinance",
                "symbol": "AAPL",
                "timeframe": "15m",
                "lookback": 500,
                "adjust": "qfq",
            },
        )

        assert response.status_code == 200, response.text
        adopted = _dataset_schedules(client)
        assert intraday_id in adopted
        assert adopted[intraday_id]["trigger"]["interval_seconds"] == 900


def test_sync_schedule_requires_scheduler_module(tmp_path) -> None:
    with _client(tmp_path, scheduler_enabled=False) as client:
        dataset_id = _insert_dataset(client, "AAPL", "15m")

        response = client.put(
            f"/api/v1/datasets/{dataset_id}/sync-schedule", json={"enabled": True}
        )

        assert response.status_code == 503

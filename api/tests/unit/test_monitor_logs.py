from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from xquant.api.app import create_app
from xquant.registry.sqlite import Database as SqliteDatabase


def _log(symbol: str = "600000", status: str = "ok") -> dict:
    return {
        "target_key": f"dataset:{symbol}",
        "dataset_id": f"dataset-{symbol}",
        "symbol": symbol,
        "timeframe": "1d",
        "status": status,
        "session_id": "2026-09-17",
        "message": "分析完成：LONG · 72%",
        "detail": {"action": "LONG", "confidence": 72.0},
    }


def test_monitor_logs_endpoints_list_filter_delete_and_clear(tmp_path: Path) -> None:
    app = create_app(tmp_path / "xquant.db")
    with TestClient(app) as client:
        database = app.state.db
        database.save_monitor_log(_log("600000", "ok"))
        database.save_monitor_log(_log("600519", "idle"))

        listed = client.get("/api/v1/ai/monitor/logs").json()
        assert listed["total"] == 2
        assert listed["limit"] == 100
        assert {item["symbol"] for item in listed["items"]} == {"600000", "600519"}

        filtered = client.get("/api/v1/ai/monitor/logs", params={"symbol": "600519"}).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["status"] == "idle"

        by_status = client.get("/api/v1/ai/monitor/logs", params={"status": "ok"}).json()
        assert by_status["total"] == 1
        assert by_status["items"][0]["symbol"] == "600000"

        log_id = by_status["items"][0]["id"]
        assert client.delete(f"/api/v1/ai/monitor/logs/{log_id}").json() == {
            "deleted": True,
            "id": log_id,
        }
        assert client.get("/api/v1/ai/monitor/logs").json()["total"] == 1
        assert client.delete(f"/api/v1/ai/monitor/logs/{log_id}").status_code == 404

        cleared = client.delete("/api/v1/ai/monitor/logs")
        assert cleared.json() == {"cleared": True}
        assert client.get("/api/v1/ai/monitor/logs").json()["total"] == 0


def test_monitor_logs_clear_can_be_scoped_by_symbol(tmp_path: Path) -> None:
    app = create_app(tmp_path / "xquant.db")
    with TestClient(app) as client:
        database = app.state.db
        database.save_monitor_log(_log("600000", "ok"))
        database.save_monitor_log(_log("600519", "ok"))

        assert client.delete(
            "/api/v1/ai/monitor/logs", params={"symbol": "600000"}
        ).json() == {"cleared": True}

        remaining = client.get("/api/v1/ai/monitor/logs").json()
        assert remaining["total"] == 1
        assert remaining["items"][0]["symbol"] == "600519"


def test_sqlite_monitor_logs_prune_expired_entries(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "quant.db")
    expired = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    conn = database._connect()
    conn.execute(
        """
        INSERT INTO monitor_run_logs
            (id, created_at, target_key, dataset_id, symbol, timeframe, status,
             session_id, message, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("expired-log", expired, None, None, "600000", "1d", "ok", None, "旧日志", "{}"),
    )
    conn.commit()
    conn.close()

    database.save_monitor_log(_log("600519", "ok"))

    items = database.list_monitor_logs()
    assert database.count_monitor_logs() == 1
    assert items[0]["symbol"] == "600519"

from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from xquant.ai.coordinator import MonitorManager, _build_monitor_schedule_state
from xquant.system_config.settings import MonitorScheduleSettings, SystemConfigStore


class _MonitorDb:
    def __init__(self, *, fail_save: bool = False) -> None:
        self.fail_save = fail_save
        self.saved: list[tuple[dict, str | None]] = []
        self.logs: list[dict] = []

    def get_dataset(self, dataset_id: str) -> dict:
        return {
            "summary": {
                "id": dataset_id,
                "symbol": "600000",
                "timeframe": "1d",
                "last_session": "2026-09-11",
            },
            "bars": [],
        }

    def save_analysis_record(
        self,
        record: dict,
        dataset_id: str | None = None,
    ) -> dict:
        if self.fail_save:
            raise RuntimeError("storage unavailable")
        self.saved.append((record, dataset_id))
        return {"id": "saved-record", "dataset_id": dataset_id}

    def save_monitor_log(self, entry: dict) -> dict:
        self.logs.append(dict(entry))
        return {**entry, "id": f"log-{len(self.logs)}"}


def _shanghai(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


def test_monitor_schedule_is_in_system_config_payloads(tmp_path) -> None:
    store = SystemConfigStore(tmp_path / "system-settings.json")

    public = store.public_payload()
    assert public["monitor_schedule"] == {
        "mode": "always",
        "timezone": "Asia/Shanghai",
        "enabled": True,
        "weekdays": [1, 2, 3, 4, 5],
        "custom_start": "09:30",
        "custom_end": "15:00",
    }

    store.update(
        {
            "monitor_schedule": {
                "mode": "custom",
                "weekdays": [5, 1, 1],
                "custom_start": "10:00",
                "custom_end": "14:30",
            }
        }
    )
    assert store.settings.monitor_schedule.weekdays == [1, 5]

    merged = store.merge_provider_payload(
        {"monitor_schedule": {"enabled": False}}
    )
    assert merged["monitor_schedule"]["mode"] == "custom"
    assert merged["monitor_schedule"]["enabled"] is False
    assert merged["monitor_schedule"]["weekdays"] == [1, 5]


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "weekdays"},
        {"timezone": "Not/AZone"},
        {"weekdays": []},
        {"weekdays": [0]},
        {"weekdays": [8]},
        {"custom_start": "9:30"},
        {"custom_start": "24:00"},
        {"custom_start": "15:00", "custom_end": "09:30"},
    ],
)
def test_monitor_schedule_rejects_invalid_configuration(payload) -> None:
    with pytest.raises(ValueError):
        MonitorScheduleSettings.model_validate(payload)


def test_a_share_schedule_handles_lunch_break_and_weekend() -> None:
    schedule = MonitorScheduleSettings(mode="a_share")

    active = _build_monitor_schedule_state(
        schedule,
        _shanghai(2026, 9, 14, 10, 0),
        interval_seconds=3600,
    )
    assert active["schedule_active_now"] is True
    assert active["schedule_label"] == "A股交易时段"
    assert active["next_check_at"] == "2026-09-14T11:00:00+08:00"

    lunch = _build_monitor_schedule_state(
        schedule,
        _shanghai(2026, 9, 14, 12, 0),
        interval_seconds=60,
    )
    assert lunch["schedule_active_now"] is False
    assert lunch["next_check_at"] == "2026-09-14T13:00:00+08:00"

    weekend = _build_monitor_schedule_state(
        schedule,
        _shanghai(2026, 9, 13, 12, 0),
        interval_seconds=60,
    )
    assert weekend["schedule_active_now"] is False
    assert weekend["next_check_at"] == "2026-09-14T09:30:00+08:00"


def test_custom_schedule_uses_configured_weekdays_and_time_range() -> None:
    schedule = MonitorScheduleSettings(
        mode="custom",
        weekdays=[1, 3, 5],
        custom_start="10:30",
        custom_end="12:00",
    )

    before_window = _build_monitor_schedule_state(
        schedule,
        _shanghai(2026, 9, 14, 10, 0),
        interval_seconds=60,
    )
    assert before_window["schedule_active_now"] is False
    assert before_window["next_check_at"] == "2026-09-14T10:30:00+08:00"

    inside_window = _build_monitor_schedule_state(
        schedule,
        _shanghai(2026, 9, 14, 11, 0),
        interval_seconds=60,
    )
    assert inside_window["schedule_active_now"] is True

    next_configured_day = _build_monitor_schedule_state(
        schedule,
        _shanghai(2026, 9, 15, 11, 0),
        interval_seconds=60,
    )
    assert next_configured_day["schedule_active_now"] is False
    assert next_configured_day["next_check_at"] == "2026-09-16T10:30:00+08:00"


def test_always_and_disabled_schedules_run_without_time_limits() -> None:
    always = _build_monitor_schedule_state(
        MonitorScheduleSettings(mode="always"),
        _shanghai(2026, 9, 13, 12, 0),
        interval_seconds=60,
    )
    assert always["schedule_active_now"] is True
    assert always["schedule_label"] == "24小时盯盘"

    disabled = _build_monitor_schedule_state(
        MonitorScheduleSettings(
            mode="custom",
            enabled=False,
            weekdays=[1],
            custom_start="09:30",
            custom_end="10:00",
        ),
        _shanghai(2026, 9, 13, 12, 0),
        interval_seconds=60,
    )
    assert disabled["schedule_active_now"] is True
    assert disabled["schedule"]["enabled"] is False
    assert disabled["schedule"]["mode"] == "custom"


def test_monitor_status_and_manual_run_once_keep_schedule_state() -> None:
    fixed_now = _shanghai(2026, 9, 13, 12, 0)
    manager = MonitorManager(object(), now_provider=lambda: fixed_now)
    manager._targets = [
        {
            "symbol": "600000",
            "source": "akshare",
            "timeframe": "1d",
        }
    ]
    manager._schedule = MonitorScheduleSettings(
        mode="custom",
        weekdays=[1],
        custom_start="09:30",
        custom_end="10:00",
    )

    status = manager.status()
    assert status["schedule_active_now"] is False
    assert status["current_time"] == "2026-09-13T12:00:00+08:00"
    assert status["next_check_at"] == "2026-09-14T09:30:00+08:00"
    assert status["items"][0]["last_record"] is None

    calls: list[str] = []

    def record_once(target, payload):
        calls.append(str(target["symbol"]))
        return {"key": "manual", "status": "ok"}

    manager._poll_target = record_once  # type: ignore[method-assign]
    result = manager.run_once()
    assert calls == ["600000"]
    assert result["status"] == "ok"


def test_monitor_start_accepts_and_normalizes_schedule() -> None:
    fixed_now = _shanghai(2026, 9, 13, 12, 0)
    manager = MonitorManager(object(), now_provider=lambda: fixed_now)

    started = manager.start(
        [{"symbol": "600000"}],
        {},
        interval_seconds=3600,
        schedule={
            "mode": "custom",
            "weekdays": [5, 1],
            "custom_start": "10:30",
            "custom_end": "12:00",
        },
    )
    try:
        assert started["schedule"] == {
            "mode": "custom",
            "timezone": "Asia/Shanghai",
            "enabled": True,
            "weekdays": [1, 5],
            "custom_start": "10:30",
            "custom_end": "12:00",
        }
        assert started["schedule_active_now"] is False
        assert started["next_check_at"] == "2026-09-14T10:30:00+08:00"
    finally:
        manager.stop()


def test_poll_target_persists_analysis_record_with_dataset_id() -> None:
    db = _MonitorDb()
    manager = MonitorManager(db)
    manager._targets = [{"dataset_id": "dataset-1"}]
    manager.batch.analyze = lambda *_args, **_kwargs: {
        "items": [
            {
                "status": "ok",
                "record": {"status": "ok", "symbol": "600000"},
            }
        ]
    }

    result = manager._poll_target({"dataset_id": "dataset-1"}, {})

    assert result["status"] == "ok"
    assert db.saved == [
        ({"status": "ok", "symbol": "600000"}, "dataset-1")
    ]
    item = manager.status()["items"][0]
    assert item["success_count"] == 1
    assert item["failure_count"] == 0
    assert item["skip_count"] == 0


def test_poll_target_keeps_running_when_record_persistence_fails(caplog) -> None:
    db = _MonitorDb(fail_save=True)
    notifications: list[dict] = []
    manager = MonitorManager(
        db,
        notification_callback=lambda record, _target, _state: notifications.append(
            dict(record)
        ),
    )
    manager._auto_notify = True
    manager.batch.analyze = lambda *_args, **_kwargs: {
        "items": [
            {
                "status": "ok",
                "record": {"status": "ok", "symbol": "600000"},
            }
        ]
    }

    with caplog.at_level(logging.WARNING):
        result = manager._poll_target({"dataset_id": "dataset-1"}, {})

    assert result["status"] == "ok"
    assert notifications == [{"status": "ok", "symbol": "600000"}]
    assert "保存盯盘分析记录失败" in caplog.text
    assert manager._status["dataset:dataset-1"]["success_count"] == 1
    assert manager._status["dataset:dataset-1"]["failure_count"] == 0


def test_poll_target_counts_idle_checks_as_skips() -> None:
    manager = MonitorManager(_MonitorDb())
    manager.batch.analyze = lambda *_args, **_kwargs: {
        "items": [{"status": "ok", "record": {"status": "ok"}}]
    }

    first = manager._poll_target({"dataset_id": "dataset-1"}, {})
    second = manager._poll_target({"dataset_id": "dataset-1"}, {})

    assert first["status"] == "ok"
    assert second["status"] == "idle"
    state = manager._status["dataset:dataset-1"]
    assert state["success_count"] == 1
    assert state["skip_count"] == 1


def test_run_once_polls_all_targets_concurrently() -> None:
    manager = MonitorManager(object())
    manager._targets = [{"symbol": f"S{index}"} for index in range(4)]
    # 屏障只有四个目标同时开跑才能通过；串行执行会超时并让测试失败。
    barrier = threading.Barrier(4, timeout=5)
    started: list[str] = []
    lock = threading.Lock()

    def fake_poll(target, _payload):
        barrier.wait()
        with lock:
            started.append(str(target["symbol"]))
        return {"key": str(target["symbol"]), "status": "ok"}

    manager._poll_target = fake_poll  # type: ignore[method-assign]
    result = manager.run_once()

    assert result["status"] == "ok"
    assert result["concurrency"] == 4
    assert sorted(started) == ["S0", "S1", "S2", "S3"]
    assert len(result["items"]) == 4


def test_poll_target_writes_monitor_logs_for_success_and_idle() -> None:
    db = _MonitorDb()
    manager = MonitorManager(db)
    manager.batch.analyze = lambda *_args, **_kwargs: {
        "items": [
            {
                "status": "ok",
                "duration_ms": 42.0,
                "record": {
                    "id": "record-1",
                    "status": "ok",
                    "symbol": "600000",
                    "stage2_decision": {
                        "decision": {"action": "LONG", "confidence": 72.0}
                    },
                },
            }
        ]
    }

    first = manager._poll_target({"dataset_id": "dataset-1"}, {})
    second = manager._poll_target({"dataset_id": "dataset-1"}, {})

    assert first["status"] == "ok"
    assert second["status"] == "idle"
    assert [entry["status"] for entry in db.logs] == ["ok", "idle"]
    success = db.logs[0]
    assert success["symbol"] == "600000"
    assert success["timeframe"] == "1d"
    assert success["dataset_id"] == "dataset-1"
    assert success["message"] == "分析完成：LONG · 72%"
    assert success["detail"]["action"] == "LONG"
    assert success["detail"]["confidence"] == 72.0
    assert db.logs[1]["message"] == "无新增已收盘K线，跳过分析"


def test_poll_target_logs_errors_and_keeps_running() -> None:
    db = _MonitorDb()
    manager = MonitorManager(db)

    def boom(*_args, **_kwargs):
        raise RuntimeError("远程行情失败")

    manager.batch._resolve_dataset = boom  # type: ignore[method-assign]
    result = manager._poll_target({"symbol": "600000"}, {})

    assert result["status"] == "error"
    assert db.logs and db.logs[0]["status"] == "error"
    assert "远程行情失败" in str(db.logs[0]["message"])


def test_monitor_concurrency_comes_from_payload() -> None:
    fixed_now = _shanghai(2026, 9, 13, 12, 0)
    manager = MonitorManager(object(), now_provider=lambda: fixed_now)
    started = manager.start(
        [{"symbol": "600000"}],
        {"monitor_concurrency": 3},
        interval_seconds=60,
    )
    try:
        assert manager._concurrency == 3
        assert started["running"] is True
    finally:
        manager.stop()

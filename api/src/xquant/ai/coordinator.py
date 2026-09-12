"""Concurrent batch analysis and poll-based realtime monitoring."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from xquant.ai.service import build_snapshot, normalize_ai_settings, run_two_stage
from xquant.system_config.settings import MonitorScheduleSettings

logger = logging.getLogger(__name__)

_A_SHARE_WINDOWS = (
    (dt_time(9, 30), dt_time(11, 30)),
    (dt_time(13, 0), dt_time(15, 0)),
)
_A_SHARE_WEEKDAYS = {1, 2, 3, 4, 5}
_SCHEDULE_LABELS = {
    "always": "24小时盯盘",
    "a_share": "A股交易时段",
    "custom": "自定义盯盘",
}


def _target_key(target: Mapping[str, Any]) -> str:
    dataset_id = str(target.get("dataset_id") or "").strip()
    if dataset_id:
        return f"dataset:{dataset_id}"
    return (
        f"{str(target.get('source') or '').strip().lower()}:"
        f"{str(target.get('symbol') or '').strip().upper()}:"
        f"{str(target.get('timeframe') or '').strip().lower()}:"
        f"{str(target.get('exchange') or '').strip().upper()}"
    )


def _target_payload(target: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in target.items()}


def _schedule_windows(
    schedule: MonitorScheduleSettings,
) -> tuple[tuple[dt_time, dt_time], ...]:
    if schedule.mode == "a_share":
        return _A_SHARE_WINDOWS
    return (
        (
            dt_time.fromisoformat(schedule.custom_start),
            dt_time.fromisoformat(schedule.custom_end),
        ),
    )


def _schedule_weekdays(schedule: MonitorScheduleSettings) -> set[int]:
    if schedule.mode == "a_share":
        return _A_SHARE_WEEKDAYS
    return set(schedule.weekdays)


def _next_allowed_checkpoint(
    local_now: datetime,
    windows: tuple[tuple[dt_time, dt_time], ...],
    weekdays: set[int],
    timezone: ZoneInfo,
) -> datetime:
    for day_offset in range(15):
        candidate_date = local_now.date() + timedelta(days=day_offset)
        if candidate_date.isoweekday() not in weekdays:
            continue
        for window_start, _window_end in windows:
            candidate = datetime.combine(
                candidate_date,
                window_start,
                tzinfo=timezone,
            )
            if candidate > local_now:
                return candidate
    raise ValueError("监控调度没有可用的下次检查时间")


def _build_monitor_schedule_state(
    schedule: MonitorScheduleSettings | Mapping[str, Any],
    now: datetime,
    interval_seconds: int,
) -> dict[str, Any]:
    """Evaluate a monitor schedule at a supplied time for API responses and tests."""

    normalized = MonitorScheduleSettings.model_validate(schedule)
    now_aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if normalized.mode == "a_share":
        timezone = ZoneInfo("Asia/Shanghai")
    else:
        timezone = ZoneInfo(normalized.timezone)
    local_now = now_aware.astimezone(timezone)

    if not normalized.enabled or normalized.mode == "always":
        active = True
        next_check = now_aware + timedelta(seconds=max(1, interval_seconds))
    else:
        windows = _schedule_windows(normalized)
        active = (
            local_now.isoweekday() in _schedule_weekdays(normalized)
            and any(
                window_start <= local_now.time() <= window_end
                for window_start, window_end in windows
            )
        )
        if active:
            next_check = now_aware + timedelta(seconds=max(1, interval_seconds))
        else:
            next_check = _next_allowed_checkpoint(
                local_now,
                windows,
                _schedule_weekdays(normalized),
                timezone,
            )

    return {
        "schedule": normalized.model_dump(),
        "schedule_active_now": active,
        "schedule_label": _SCHEDULE_LABELS[normalized.mode],
        "next_check_at": next_check.astimezone(timezone).isoformat(),
        "current_time": local_now.isoformat(),
    }


class BatchAnalyzer:
    """Resolve targets and run the two-stage engine with bounded concurrency."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def _resolve_dataset(self, target: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        dataset_id = str(target.get("dataset_id") or "").strip()
        if dataset_id:
            return self.db.get_dataset(dataset_id), None
        source = str(target.get("source") or "yfinance").strip().lower()
        symbol = str(target.get("symbol") or "").strip()
        timeframe = str(target.get("timeframe") or "1d").strip().lower()
        if not symbol:
            raise ValueError("监控标的必须提供 dataset_id 或 symbol")
        sync_result = self.db.sync_dataset(
            {
                "source": source,
                "symbol": symbol,
                "timeframe": timeframe,
                "lookback": int(target.get("lookback") or 500),
                "adjust": str(target.get("adjust") or "qfq"),
                "exchange": str(target.get("exchange") or ""),
            }
        )
        return self.db.get_dataset(str(sync_result["id"])), sync_result

    def _analyze_target(
        self,
        index: int,
        target: Mapping[str, Any],
        global_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            dataset, sync_result = self._resolve_dataset(target)
            target_analysis = target.get("analysis")
            merged: dict[str, Any] = dict(global_payload)
            if isinstance(target_analysis, Mapping):
                merged.update(target_analysis)
            for key in (
                "analysis_bar_count",
                "decision_stance",
                "enable_next_bar_prediction",
                "keep_analysis",
                "incremental_max_new_bars",
            ):
                if key in target:
                    merged[key] = target[key]
            settings = normalize_ai_settings(merged)
            snapshot = build_snapshot(
                dataset_id=str(dataset["summary"]["id"]),
                symbol=str(dataset["summary"]["symbol"]),
                timeframe=str(dataset["summary"]["timeframe"]),
                bars=dataset["bars"],
                settings=settings,
            )
            record = run_two_stage(snapshot, settings)
            return {
                "index": index,
                "target": _target_payload(target),
                "dataset": dataset["summary"],
                "sync": sync_result,
                "status": "ok" if record.get("status") == "ok" else "error",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "record": record,
                "error": record.get("exception"),
            }
        except Exception as exc:  # noqa: BLE001 - one target must not abort the batch
            logger.warning("批量分析失败 %s: %s", _target_key(target), exc)
            return {
                "index": index,
                "target": _target_payload(target),
                "status": "error",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }

    def analyze(
        self,
        targets: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any] | None = None,
        *,
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        clean_targets = [dict(target) for target in targets if isinstance(target, Mapping)]
        if not clean_targets:
            raise ValueError("至少需要一个分析标的")
        requested = concurrency or payload.get("concurrency") if payload else concurrency
        try:
            workers = max(1, min(8, int(requested or 3)))
        except (TypeError, ValueError):
            workers = 3
        started = time.perf_counter()
        results: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="xquant-batch") as pool:
            futures = {
                pool.submit(self._analyze_target, index, target, dict(payload or {})): index
                for index, target in enumerate(clean_targets)
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        items = [results[index] for index in range(len(clean_targets))]
        ok_count = sum(1 for item in items if item["status"] == "ok")
        return {
            "status": "ok" if ok_count == len(items) else "partial" if ok_count else "error",
            "summary": {
                "total": len(items),
                "succeeded": ok_count,
                "failed": len(items) - ok_count,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "concurrency": workers,
            },
            "items": items,
        }


class MonitorManager:
    """Poll datasets and analyze only when a new closed session appears."""

    def __init__(
        self,
        db: Any,
        *,
        notification_callback: Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Any]
        | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.batch = BatchAnalyzer(db)
        self.notification_callback = notification_callback
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._targets: list[dict[str, Any]] = []
        self._payload: dict[str, Any] = {}
        self._schedule = MonitorScheduleSettings()
        self._interval_seconds = 60
        self._auto_notify = False
        self._status: dict[str, dict[str, Any]] = {}
        self._target_locks: dict[str, threading.Lock] = {}
        self._last_cycle_at: str | None = None
        self._running = False

    def start(
        self,
        targets: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any] | None = None,
        *,
        interval_seconds: int | None = None,
        auto_notify: bool = False,
        schedule: Mapping[str, Any] | MonitorScheduleSettings | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.status()
            self._targets = [dict(target) for target in targets if isinstance(target, Mapping)]
            if not self._targets:
                raise ValueError("至少需要一个监控标的")
            self._payload = dict(payload or {})
            analysis = self._payload.get("analysis")
            if not isinstance(analysis, Mapping):
                analysis = {}
            configured_interval = analysis.get("monitor_interval_seconds")
            try:
                self._interval_seconds = max(
                    1,
                    min(86400, int(interval_seconds or configured_interval or 60)),
                )
            except (TypeError, ValueError):
                self._interval_seconds = 60
            self._schedule = MonitorScheduleSettings.model_validate(schedule or {})
            self._payload["monitor_schedule"] = self._schedule.model_dump()
            self._auto_notify = bool(auto_notify)
            self._stop_event.clear()
            self._running = True
            self._status = {
                _target_key(target): {
                    "target": _target_payload(target),
                    "status": "pending",
                    "last_session": None,
                    "last_run_at": None,
                    "last_status": None,
                    "last_error": None,
                    "last_record": None,
                    "run_count": 0,
                    "new_bar_count": 0,
                }
                for target in self._targets
            }
            self._target_locks = {
                _target_key(target): threading.Lock() for target in self._targets
            }
            self._thread = threading.Thread(
                target=self._run_loop,
                name="xquant-monitor",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def stop(self, *, wait: bool = True) -> dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if wait and thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)
        with self._lock:
            self._running = False
            self._thread = None
        return self.status()

    def run_once(self) -> dict[str, Any]:
        with self._lock:
            targets = list(self._targets)
            payload = dict(self._payload)
        if not targets:
            return self.status()
        results: list[dict[str, Any]] = []
        for target in targets:
            results.append(self._poll_target(target, payload))
        with self._lock:
            self._last_cycle_at = datetime.now(UTC).isoformat()
        return {"status": "ok", "items": results, "cycle_at": self._last_cycle_at}

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = {
                "running": self._running,
                "interval_seconds": self._interval_seconds,
                "auto_notify": self._auto_notify,
                "last_cycle_at": self._last_cycle_at,
                "items": [
                    self._status.get(
                        _target_key(target),
                        {
                            "target": _target_payload(target),
                            "status": "unknown",
                            "last_session": None,
                            "last_run_at": None,
                            "last_status": None,
                            "last_error": None,
                            "last_record": None,
                            "run_count": 0,
                            "new_bar_count": 0,
                        },
                    )
                    for target in self._targets
                ],
            }
            payload.update(self._schedule_snapshot())
            return payload

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            schedule_state = self._schedule_snapshot()
            if not schedule_state["schedule_active_now"]:
                current = self._now()
                next_check = datetime.fromisoformat(schedule_state["next_check_at"])
                wait_seconds = max(0.0, (next_check - current).total_seconds())
                self._stop_event.wait(wait_seconds or 0.1)
                continue
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:
                logger.exception("监控轮询失败")
            remaining = self._interval_seconds - (time.monotonic() - started)
            if remaining > 0:
                self._stop_event.wait(remaining)

    def _now(self) -> datetime:
        value = self._now_provider()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def _schedule_snapshot(self) -> dict[str, Any]:
        return _build_monitor_schedule_state(
            self._schedule,
            self._now(),
            self._interval_seconds,
        )

    def _poll_target(self, target: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        key = _target_key(target)
        lock = self._target_locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            return {"key": key, "status": "running", "skipped": True}
        with self._lock:
            state = self._status.setdefault(
                key,
                {
                    "target": _target_payload(target),
                    "status": "pending",
                    "last_session": None,
                    "last_run_at": None,
                    "last_status": None,
                    "last_error": None,
                    "last_record": None,
                    "run_count": 0,
                    "new_bar_count": 0,
                },
            )
        try:
            try:
                dataset, sync_result = self.batch._resolve_dataset(target)
                summary = dataset["summary"]
                latest_session = str(summary.get("last_session") or "")
                previous_session = state.get("last_session")
                inserted_count = int((sync_result or {}).get("inserted_count") or 0)
                first_run = previous_session is None
                has_new_bar = (
                    first_run
                    or not previous_session
                    or latest_session != previous_session
                    or inserted_count > 0
                )
                with self._lock:
                    state.update(
                        {
                            "status": "running",
                            "last_session": latest_session,
                            "last_run_at": datetime.now(UTC).isoformat(),
                        }
                    )
                if not has_new_bar:
                    with self._lock:
                        state.update({"status": "idle", "last_status": "idle"})
                    return {"key": key, "status": "idle", "last_session": latest_session}
                merged: dict[str, Any] = dict(payload)
                target_analysis = target.get("analysis")
                if isinstance(target_analysis, Mapping):
                    merged.update(target_analysis)
                result = self.batch.analyze([target], merged, concurrency=1)
                item = result["items"][0]
                record = item.get("record") or {}
                with self._lock:
                    state.update(
                        {
                            "status": item["status"],
                            "last_status": item["status"],
                            "last_error": item.get("error"),
                            "last_record": record,
                            "run_count": int(state.get("run_count") or 0) + 1,
                            "new_bar_count": int(state.get("new_bar_count") or 0)
                            + (1 if has_new_bar else 0),
                            "last_run_at": datetime.now(UTC).isoformat(),
                        }
                    )
                save_record = getattr(self.db, "save_analysis_record", None)
                if callable(save_record) and record:
                    try:
                        save_record(
                            dict(record),
                            dataset_id=str(summary.get("id") or "") or None,
                        )
                    except Exception as exc:  # noqa: BLE001 - persistence is non-fatal
                        logger.warning("保存盯盘分析记录失败 %s: %s", key, exc)
                if self._auto_notify and self.notification_callback and record.get("status") == "ok":
                    try:
                        self.notification_callback(record, target, state)
                    except Exception as exc:  # noqa: BLE001 - notification failure is non-fatal
                        logger.warning("监控通知失败 %s: %s", key, exc)
                return {
                    "key": key,
                    "status": item["status"],
                    "last_session": latest_session,
                    "record": record,
                }
            except Exception as exc:  # noqa: BLE001 - isolate each target
                logger.warning("监控目标失败 %s: %s", key, exc)
                with self._lock:
                    state.update(
                        {
                            "status": "error",
                            "last_status": "error",
                            "last_error": {"type": type(exc).__name__, "message": str(exc)},
                            "last_run_at": datetime.now(UTC).isoformat(),
                        }
                    )
                return {
                    "key": key,
                    "status": "error",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
        finally:
            lock.release()

"""数据中心自动同步计划：按数据集（symbol + timeframe）维护调度。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from xquant.marketdata.sync_policy import (
    DATASET_SYNC_TASK,
    DEFAULT_ADJUST,
    DEFAULT_LOOKBACK,
    DEFAULT_MAX_CATCH_UP_RUNS,
    SYNC_TIMEZONE,
    build_dataset_sync_params,
    clamp_interval_seconds,
    dataset_sync_schedule_id,
    default_interval_seconds,
    is_dataset_sync_params,
    is_legacy_sync_params,
    is_remote_source,
    legacy_entries,
    normalize_interval_seconds,
    params_dataset_id,
)
from xquant.scheduler.domain import (
    IntervalTrigger,
    MisfirePolicy,
    ScheduleDefinition,
)

logger = logging.getLogger(__name__)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _interval_seconds(schedule: ScheduleDefinition) -> int | None:
    trigger = schedule.trigger
    seconds = getattr(trigger, "interval", None)
    if seconds is None:
        return None
    total = getattr(seconds, "total_seconds", None)
    return int(total()) if callable(total) else None


def _find_dataset_schedule(
    schedules: list[ScheduleDefinition],
    dataset_id: str,
) -> ScheduleDefinition | None:
    for schedule in schedules:
        if is_dataset_sync_params(schedule.params) and params_dataset_id(schedule.params) == dataset_id:
            return schedule
    return None


def _build_schedule(
    dataset: Mapping[str, Any],
    *,
    interval_seconds: int,
    lookback: Any,
    adjust: Any,
    enabled: bool,
    max_catch_up_runs: int = DEFAULT_MAX_CATCH_UP_RUNS,
    schedule_id: str | None = None,
) -> ScheduleDefinition:
    return ScheduleDefinition(
        id=schedule_id or dataset_sync_schedule_id(dataset["symbol"], dataset["timeframe"]),
        task_name=DATASET_SYNC_TASK,
        trigger=IntervalTrigger(interval_seconds=interval_seconds, timezone=SYNC_TIMEZONE),
        params=build_dataset_sync_params(dataset, lookback=lookback, adjust=adjust),
        timezone=SYNC_TIMEZONE,
        misfire_policy=MisfirePolicy.FIRE_ONCE,
        enabled=enabled,
        max_catch_up_runs=max_catch_up_runs,
    )


class DatasetSyncScheduleService:
    """把「自动同步」维护成数据集级计划，并负责与数据集真源对账。"""

    def __init__(self, database: Any, runtime: Any) -> None:
        self.database = database
        self.runtime = runtime

    # ------------------------------------------------------------------ 对外操作

    async def set_dataset_sync(
        self,
        dataset_id: str,
        *,
        enabled: bool,
        interval_seconds: Any = None,
        lookback: Any = None,
        adjust: Any = None,
    ) -> dict[str, Any]:
        dataset = self._dataset(dataset_id)
        if not is_remote_source(dataset.get("source")):
            raise ValueError("该数据集来自本地文件，不能启用自动同步")

        datasets = {str(item["id"]): item for item in self.database.list_datasets()}
        datasets[str(dataset["id"])] = dataset
        schedules = await self._schedules()
        if await self._split_legacy(schedules, datasets):
            schedules = await self._schedules()
        current = _find_dataset_schedule(schedules, str(dataset["id"]))

        if not enabled:
            if current is None:
                return {
                    "dataset_id": str(dataset["id"]),
                    "schedule_id": None,
                    "enabled": False,
                    "interval_seconds": None,
                }
            saved = await self._save(replace(current, enabled=False))
            return self._describe(saved)

        resolved_interval = normalize_interval_seconds(
            dataset.get("timeframe"),
            interval_seconds
            if interval_seconds is not None
            else (_interval_seconds(current) if current is not None else None),
        )
        resolved_lookback = self._resolve_value(
            lookback, current, "lookback", DEFAULT_LOOKBACK
        )
        resolved_adjust = self._resolve_value(adjust, current, "adjust", DEFAULT_ADJUST)

        if current is None:
            schedule = _build_schedule(
                dataset,
                interval_seconds=resolved_interval,
                lookback=resolved_lookback,
                adjust=resolved_adjust,
                enabled=True,
            )
        else:
            schedule = replace(
                current,
                trigger=IntervalTrigger(
                    interval_seconds=resolved_interval, timezone=SYNC_TIMEZONE
                ),
                params=build_dataset_sync_params(
                    dataset, lookback=resolved_lookback, adjust=resolved_adjust
                ),
                enabled=True,
            )
        saved = await self._save(schedule)
        # 该标的已开启自动同步时，其他周期也一并纳入（新增周期不会漏同步）。
        await self._adopt_missing(datasets, await self._schedules())
        return self._describe(saved)

    async def reconcile(self) -> dict[str, Any]:
        """以数据集列表为真源，修复自动同步计划。"""

        datasets = {str(item["id"]): item for item in self.database.list_datasets()}
        schedules = await self._schedules()

        migrated = await self._split_legacy(schedules, datasets)
        if migrated:
            schedules = await self._schedules()

        created: list[str] = []
        updated: list[str] = []
        deleted: list[str] = []

        for schedule in schedules:
            params = schedule.params
            if not (is_dataset_sync_params(params) or is_legacy_sync_params(params)):
                continue
            dataset_id = params_dataset_id(params)
            if dataset_id is None:
                # 旧计划的 dataset 都不存在了，直接清掉。
                await self._delete(schedule.id)
                deleted.append(schedule.id)
                continue
            dataset = datasets.get(dataset_id)
            if dataset is None:
                await self._delete(schedule.id)
                deleted.append(schedule.id)
                continue
            desired = build_dataset_sync_params(
                dataset,
                lookback=params.get("lookback") or DEFAULT_LOOKBACK,
                adjust=params.get("adjust") or DEFAULT_ADJUST,
            )
            if dict(params) != desired:
                await self._save(replace(schedule, params=desired), recompute_next=False)
                updated.append(dataset_id)

        schedules = await self._schedules()
        created.extend(await self._adopt_missing(datasets, schedules))

        return {
            "created": [*migrated, *created],
            "updated": updated,
            "deleted": deleted,
        }

    async def adopt_symbol(self, symbol: Any) -> dict[str, Any]:
        """同步完成后，把已经开启自动同步的标的新周期自动纳入计划。"""

        target = _normalize_symbol(symbol)
        if not target:
            return {"created": []}
        datasets = {
            str(item["id"]): item
            for item in self.database.list_datasets()
            if _normalize_symbol(item.get("symbol")) == target
        }
        if not datasets:
            return {"created": []}
        schedules = await self._schedules()
        return {"created": await self._adopt_missing(datasets, schedules)}

    async def delete_for_dataset(self, dataset_id: str) -> dict[str, int]:
        target = str(dataset_id)
        schedules = await self._schedules()
        removed = 0
        for schedule in schedules:
            params = schedule.params
            if is_dataset_sync_params(params):
                if params_dataset_id(params) == target:
                    await self._delete(schedule.id)
                    removed += 1
                continue
            if not is_legacy_sync_params(params):
                continue
            entries = legacy_entries(params)
            kept = [
                entry
                for entry in entries
                if str(entry.get("dataset_id") or "") != target
            ]
            if len(kept) == len(entries):
                continue
            if not kept:
                await self._delete(schedule.id)
                removed += 1
                continue
            updated_params = dict(params)
            updated_params["symbols"] = kept
            updated_params["dataset_ids"] = [
                str(entry.get("dataset_id") or "") for entry in kept
            ]
            await self._save(
                replace(schedule, params=updated_params), recompute_next=False
            )
        return {"removed": removed}

    # ------------------------------------------------------------------ 内部实现

    def _dataset(self, dataset_id: str) -> dict[str, Any]:
        return self.database.get_dataset(str(dataset_id))["summary"]

    async def _schedules(self) -> list[ScheduleDefinition]:
        return list(await self.runtime.service.list_schedules())

    async def _save(
        self,
        schedule: ScheduleDefinition,
        *,
        recompute_next: bool = True,
    ) -> ScheduleDefinition:
        saved = await self.runtime.service.save_schedule(
            schedule, recompute_next=recompute_next
        )
        await self._sync_engine(saved)
        return saved

    async def _delete(self, schedule_id: str) -> None:
        await self.runtime.service.delete_schedule(schedule_id)
        engine = getattr(self.runtime, "engine", None)
        if engine is not None and getattr(engine, "running", False):
            await engine.remove_schedule(schedule_id)

    async def _sync_engine(self, schedule: ScheduleDefinition) -> None:
        engine = getattr(self.runtime, "engine", None)
        if engine is None or not getattr(engine, "running", False):
            return
        await engine.remove_schedule(schedule.id)
        if schedule.enabled:
            await engine.add_schedule(schedule)

    async def _split_legacy(
        self,
        schedules: list[ScheduleDefinition],
        datasets: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        """把旧的 symbol 级计划拆成每个数据集一条计划。"""

        created: list[str] = []
        for schedule in schedules:
            if not is_legacy_sync_params(schedule.params):
                continue
            legacy_interval = _interval_seconds(schedule)
            for entry in legacy_entries(schedule.params):
                dataset = datasets.get(str(entry.get("dataset_id") or ""))
                if dataset is None or not is_remote_source(dataset.get("source")):
                    continue
                if _find_dataset_schedule(schedules, str(dataset["id"])) is not None:
                    continue
                schedule_id = dataset_sync_schedule_id(
                    dataset["symbol"], dataset["timeframe"]
                )
                await self._save(
                    _build_schedule(
                        dataset,
                        interval_seconds=clamp_interval_seconds(
                            dataset.get("timeframe"), legacy_interval
                        ),
                        lookback=entry.get("lookback") or DEFAULT_LOOKBACK,
                        adjust=entry.get("adjust") or DEFAULT_ADJUST,
                        enabled=bool(schedule.enabled),
                        max_catch_up_runs=schedule.max_catch_up_runs,
                        schedule_id=schedule_id,
                    )
                )
                created.append(str(dataset["id"]))
            await self._delete(schedule.id)
        return created

    async def _adopt_missing(
        self,
        datasets: Mapping[str, Mapping[str, Any]],
        schedules: list[ScheduleDefinition],
    ) -> list[str]:
        """标的已开启自动同步时，为其新增周期自动补建计划。"""

        templates: dict[str, Mapping[str, Any]] = {}
        for schedule in schedules:
            params = schedule.params
            if not (is_dataset_sync_params(params) and schedule.enabled):
                continue
            dataset_id = params_dataset_id(params)
            dataset = datasets.get(dataset_id) if dataset_id else None
            if dataset is None:
                continue
            templates.setdefault(_normalize_symbol(dataset.get("symbol")), params)

        created: list[str] = []
        for dataset in datasets.values():
            template = templates.get(_normalize_symbol(dataset.get("symbol")))
            if template is None:
                continue
            if not is_remote_source(dataset.get("source")):
                continue
            if _find_dataset_schedule(schedules, str(dataset["id"])) is not None:
                continue
            await self._save(
                _build_schedule(
                    dataset,
                    interval_seconds=default_interval_seconds(dataset.get("timeframe")),
                    lookback=template.get("lookback") or DEFAULT_LOOKBACK,
                    adjust=template.get("adjust") or DEFAULT_ADJUST,
                    enabled=True,
                )
            )
            created.append(str(dataset["id"]))
        return created

    @staticmethod
    def _resolve_value(
        value: Any,
        current: ScheduleDefinition | None,
        key: str,
        fallback: Any,
    ) -> Any:
        if value is not None:
            return value
        if current is not None and current.params.get(key):
            return current.params.get(key)
        return fallback

    @staticmethod
    def _describe(schedule: ScheduleDefinition) -> dict[str, Any]:
        next_fire_at = schedule.next_fire_at
        return {
            "dataset_id": params_dataset_id(schedule.params),
            "schedule_id": schedule.id,
            "task_name": schedule.task_name,
            "enabled": bool(schedule.enabled),
            "interval_seconds": _interval_seconds(schedule),
            "timezone": str(schedule.timezone),
            "next_fire_at": next_fire_at.isoformat() if next_fire_at else None,
        }


__all__ = ["DatasetSyncScheduleService"]

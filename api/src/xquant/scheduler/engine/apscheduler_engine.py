from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from xquant.scheduler.application.scheduler_service import SchedulerService
from xquant.scheduler.domain import (
    CronTrigger,
    DateTrigger,
    FixedDelayTrigger,
    IntervalTrigger,
    ScheduleDefinition,
)

from .base import SchedulerEngine


class APSchedulerEngine(SchedulerEngine):
    """APScheduler adapter hidden behind the scheduler engine interface."""

    def __init__(
        self,
        service: SchedulerService,
        *,
        timezone: str | ZoneInfo = "UTC",
    ) -> None:
        self.service = service
        self.timezone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
        self._scheduler: Any | None = None
        self._fingerprints: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return bool(self._scheduler and self._scheduler.running)

    async def start(self) -> None:
        if self.running:
            return
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
        except ImportError as exc:
            raise RuntimeError(
                "APScheduler is required when scheduler.engine_type=apscheduler"
            ) from exc
        self._scheduler = AsyncIOScheduler(timezone=self.timezone)
        self._scheduler.start()
        await self.reload()
        self._scheduler.add_job(
            self.reload,
            trigger="interval",
            seconds=5,
            id="scheduler:control:poll",
            replace_existing=True,
            max_instances=1,
        )

    async def shutdown(self) -> None:
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        self._fingerprints.clear()

    async def add_schedule(self, schedule: ScheduleDefinition) -> None:
        scheduler = self._require_scheduler()
        if not schedule.enabled:
            return
        trigger = self._adapt_trigger(schedule)
        scheduler.add_job(
            self._fire,
            trigger=trigger,
            args=[schedule.id],
            id=self._job_id(schedule.id),
            name=schedule.id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60,
        )
        self._fingerprints[schedule.id] = _fingerprint(schedule)

    async def remove_schedule(self, schedule_id: str) -> None:
        scheduler = self._require_scheduler()
        job = scheduler.get_job(self._job_id(schedule_id))
        if job is not None:
            scheduler.remove_job(self._job_id(schedule_id))
        self._fingerprints.pop(schedule_id, None)

    async def pause_schedule(self, schedule_id: str) -> None:
        scheduler = self._require_scheduler()
        job = scheduler.get_job(self._job_id(schedule_id))
        if job is not None and job.next_run_time is not None:
            scheduler.pause_job(self._job_id(schedule_id))

    async def resume_schedule(self, schedule_id: str) -> None:
        scheduler = self._require_scheduler()
        job = scheduler.get_job(self._job_id(schedule_id))
        if job is None:
            schedule = await self.service.get_schedule(schedule_id)
            await self.add_schedule(schedule)
            return
        scheduler.resume_job(self._job_id(schedule_id))

    async def reload(self) -> None:
        scheduler = self._require_scheduler()
        current_time = datetime.now(UTC)
        for schedule in await self.service.list_schedules(enabled=True):
            if (
                schedule.next_fire_at is not None
                and schedule.next_fire_at <= current_time
            ):
                await self.service.fire_schedule(
                    schedule.id,
                    now=current_time,
                )
        schedules = await self.service.list_schedules(enabled=True)
        wanted = {schedule.id: _fingerprint(schedule) for schedule in schedules}
        current_jobs = {
            str(job.id): job
            for job in scheduler.get_jobs()
            if str(job.id).startswith("scheduler:")
            and job.id != "scheduler:control:poll"
        }
        for job_id in current_jobs:
            schedule_id = job_id.removeprefix("scheduler:")
            if wanted.get(schedule_id) != self._fingerprints.get(schedule_id):
                scheduler.remove_job(job_id)
        for schedule in schedules:
            schedule_id = schedule.id
            if (
                current_jobs.get(self._job_id(schedule_id)) is None
                or self._fingerprints.get(schedule_id) != wanted[schedule_id]
            ):
                await self.add_schedule(schedule)
        for schedule_id in tuple(self._fingerprints):
            if schedule_id not in wanted:
                await self.remove_schedule(schedule_id)
        self._fingerprints = wanted

    async def _fire(self, schedule_id: str) -> None:
        await self.service.fire_schedule(schedule_id)
        schedule = await self.service.get_schedule(schedule_id)
        if isinstance(schedule.trigger, DateTrigger):
            await self.remove_schedule(schedule_id)

    def _require_scheduler(self) -> Any:
        if self._scheduler is None or not self._scheduler.running:
            raise RuntimeError("scheduler engine is not running")
        return self._scheduler

    @staticmethod
    def _job_id(schedule_id: str) -> str:
        return f"scheduler:{schedule_id}"

    @staticmethod
    def _adapt_trigger(schedule: ScheduleDefinition) -> Any:
        from apscheduler.triggers.cron import CronTrigger as APSCronTrigger
        from apscheduler.triggers.date import DateTrigger as APSDateTrigger
        from apscheduler.triggers.interval import IntervalTrigger as APSIntervalTrigger

        trigger = schedule.trigger
        timezone = trigger.timezone
        if isinstance(trigger, CronTrigger):
            return APSCronTrigger.from_crontab(
                _cron_expression(trigger),
                timezone=timezone,
            )
        if isinstance(trigger, DateTrigger):
            return APSDateTrigger(
                run_date=trigger.run_at,
                timezone=timezone,
            )
        if isinstance(trigger, IntervalTrigger):
            return APSIntervalTrigger(
                seconds=trigger.interval.total_seconds(),
                start_date=schedule.next_fire_at or trigger.start_at,
                timezone=timezone,
            )
        if isinstance(trigger, FixedDelayTrigger):
            return APSIntervalTrigger(
                seconds=trigger.delay.total_seconds(),
                start_date=schedule.next_fire_at or trigger.start_at,
                timezone=timezone,
            )
        next_fire_at = schedule.next_fire_at
        if next_fire_at is None:
            raise ValueError(
                f"unsupported trigger for APScheduler: {type(trigger).__name__}"
            )
        return APSDateTrigger(run_date=next_fire_at, timezone=timezone)


def _cron_expression(trigger: CronTrigger) -> str:
    def field(parsed: Any) -> str:
        values = sorted(parsed.values)
        if parsed.wildcard:
            return "*"
        return ",".join(str(value) for value in values)

    return " ".join(
        (
            field(trigger.minute),
            field(trigger.hour),
            field(trigger.day),
            field(trigger.month),
            field(trigger.day_of_week),
        )
    )


def _fingerprint(schedule: ScheduleDefinition) -> str:
    return json.dumps(
        schedule.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


__all__ = ["APSchedulerEngine"]

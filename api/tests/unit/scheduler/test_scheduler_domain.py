from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    CronTrigger,
    DateTrigger,
    ExecutionStatus,
    FixedDelayTrigger,
    IntervalTrigger,
    MisfirePolicy,
    RetryPolicy,
    RetryStrategy,
    ScheduleDefinition,
    TaskContext,
    TaskDefinition,
    TaskEvent,
    TaskExecution,
    TaskResult,
    TradingCalendar,
    Trigger,
    from_dict,
    load_trigger,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def test_public_enums_cover_p0_and_p1_states() -> None:
    assert list(ExecutionStatus) == [
        ExecutionStatus.PENDING,
        ExecutionStatus.QUEUED,
        ExecutionStatus.RUNNING,
        ExecutionStatus.WAITING,
        ExecutionStatus.SUCCESS,
        ExecutionStatus.FAILED,
        ExecutionStatus.RETRYING,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.SKIPPED,
    ]
    assert list(ConcurrencyPolicy) == [
        ConcurrencyPolicy.ALLOW,
        ConcurrencyPolicy.FORBID,
        ConcurrencyPolicy.SERIAL,
        ConcurrencyPolicy.REPLACE,
    ]
    assert list(MisfirePolicy) == [
        MisfirePolicy.SKIP,
        MisfirePolicy.FIRE_ONCE,
        MisfirePolicy.CATCH_UP,
    ]
    assert list(RetryStrategy) == [
        RetryStrategy.FIXED,
        RetryStrategy.LINEAR,
        RetryStrategy.EXPONENTIAL,
    ]


@pytest.mark.parametrize(
    ("strategy", "attempt", "expected"),
    [
        (RetryStrategy.FIXED, 1, 5.0),
        (RetryStrategy.FIXED, 4, 5.0),
        (RetryStrategy.LINEAR, 1, 5.0),
        (RetryStrategy.LINEAR, 3, 15.0),
        (RetryStrategy.EXPONENTIAL, 1, 5.0),
        (RetryStrategy.EXPONENTIAL, 2, 10.0),
        (RetryStrategy.EXPONENTIAL, 4, 20.0),
    ],
)
def test_retry_policy_delay_strategies_and_cap(
    strategy: RetryStrategy,
    attempt: int,
    expected: float,
) -> None:
    policy = RetryPolicy(
        max_attempts=5,
        strategy=strategy,
        initial_delay_seconds=5,
        max_delay_seconds=20,
        multiplier=2,
        jitter=False,
    )
    assert policy.delay_for(attempt) == expected


def test_retry_policy_jitter_is_bounded_and_retry_boundary_is_explicit() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        strategy="fixed",
        initial_delay_seconds=10,
        max_delay_seconds=10,
        jitter=True,
    )
    assert 0 <= policy.delay_for(1) <= 10
    assert policy.can_retry(0)
    assert policy.can_retry(2)
    assert not policy.can_retry(3)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RetryPolicy(max_attempts=0),
        lambda: RetryPolicy(initial_delay_seconds=-1),
        lambda: RetryPolicy(max_delay_seconds=-1),
        lambda: RetryPolicy(multiplier=0),
        lambda: RetryPolicy().delay_for(0),
        lambda: RetryPolicy().can_retry(-1),
    ],
)
def test_retry_policy_rejects_invalid_boundaries(factory) -> None:
    with pytest.raises(ValueError):
        factory()


def test_domain_dataclasses_validate_timezone_aware_datetimes() -> None:
    definition = TaskDefinition(name="task", handler="TaskHandler")
    execution = TaskExecution(
        id="execution-1",
        task_name=definition.name,
        scheduled_at=datetime(2026, 9, 15, 9, 30, tzinfo=SHANGHAI),
    )
    context = TaskContext(
        task_name=definition.name,
        execution_id=execution.id,
        schedule_id=None,
        scheduled_at=execution.scheduled_at,
        started_at=execution.scheduled_at,
        attempt=1,
        params={},
        trace_id="trace-1",
    )
    event = TaskEvent(
        event_type="TaskStarted",
        execution_id=execution.id,
        task_name=definition.name,
        occurred_at=execution.scheduled_at,
        trace_id=context.trace_id,
    )
    assert event.occurred_at.tzinfo is not None
    assert TaskResult(success=True).data is None

    with pytest.raises(ValueError, match="timezone-aware"):
        TaskEvent(
            event_type="TaskStarted",
            execution_id=execution.id,
            task_name=definition.name,
            occurred_at=datetime(2026, 9, 15, 9, 30),  # noqa: DTZ001
            trace_id=context.trace_id,
        )


def test_schedule_definition_normalizes_policies_and_timezone() -> None:
    schedule = ScheduleDefinition(
        id="schedule-1",
        task_name="task",
        trigger=CronTrigger("*", "*", "*", "*", "*", timezone="UTC"),
        misfire_policy="CATCH_UP",
        timezone="Asia/Shanghai",
        max_catch_up_runs=5,
    )
    assert schedule.misfire_policy is MisfirePolicy.CATCH_UP
    assert schedule.timezone == SHANGHAI


@pytest.mark.parametrize(
    "trigger",
    [
        CronTrigger(
            minute="*/15,5-7",
            hour="9-10",
            day="*",
            month="*",
            day_of_week="1-5",
            timezone="Asia/Shanghai",
        ),
        IntervalTrigger(
            seconds=300,
            start_at=datetime(2026, 9, 15, 9, 0, tzinfo=SHANGHAI),
            timezone="Asia/Shanghai",
        ),
        DateTrigger(datetime(2026, 9, 15, 15, 5, tzinfo=SHANGHAI)),
        FixedDelayTrigger(timedelta(seconds=90), timezone="UTC"),
    ],
)
def test_trigger_serialization_round_trips_without_losing_type(trigger) -> None:
    payload = trigger.to_dict()
    restored = load_trigger(payload)

    assert type(restored) is type(trigger)
    assert restored.to_dict() == payload
    assert from_dict(payload).to_dict() == payload
    assert Trigger.from_dict(payload).to_dict() == payload


def test_load_trigger_accepts_repository_trigger_type_field() -> None:
    payload = {
        "trigger_type": "fixed_delay",
        "delay_seconds": 45,
        "start_at": None,
        "timezone": "UTC",
    }
    restored = load_trigger(payload)

    assert isinstance(restored, FixedDelayTrigger)
    assert restored.delay == timedelta(seconds=45)


def test_schedule_definition_round_trips_with_abstract_trigger_field() -> None:
    schedule = ScheduleDefinition(
        id="schedule-round-trip",
        task_name="task",
        trigger=CronTrigger(
            minute="*/5",
            hour="9-15",
            day="*",
            month="*",
            day_of_week="1-5",
            timezone="Asia/Shanghai",
        ),
        params={"market": "CN"},
        calendar="SSE",
        timezone="Asia/Shanghai",
        misfire_policy="CATCH_UP",
        enabled=True,
        max_catch_up_runs=10,
        last_fire_at=datetime(2026, 9, 15, 9, 0, tzinfo=SHANGHAI),
        next_fire_at=datetime(2026, 9, 15, 9, 5, tzinfo=SHANGHAI),
        created_at=datetime(2026, 9, 15, 8, 0, tzinfo=SHANGHAI),
    )
    payload = schedule.to_dict()
    restored = ScheduleDefinition.from_dict(payload)

    assert restored.to_dict() == payload
    assert isinstance(restored.trigger, CronTrigger)
    assert restored.misfire_policy is MisfirePolicy.CATCH_UP


def test_schedule_definition_round_trips_through_repository_serialization() -> None:
    from xquant.scheduler.repository._serialization import (
        dumps,
        loads,
        to_domain,
    )

    schedule = ScheduleDefinition(
        id="schedule-repository-round-trip",
        task_name="task",
        trigger=IntervalTrigger(seconds=60, timezone="Asia/Shanghai"),
        params={"scope": "all"},
        timezone="Asia/Shanghai",
    )
    restored = to_domain(loads(dumps(schedule)), ScheduleDefinition)

    assert isinstance(restored, ScheduleDefinition)
    assert isinstance(restored.trigger, IntervalTrigger)
    assert restored.to_dict() == schedule.to_dict()


def test_cron_trigger_supports_all_required_field_syntax() -> None:
    trigger = CronTrigger(
        minute="*/15,5-7",
        hour="9-10",
        day="*",
        month="*",
        day_of_week="1-5",
        timezone="Asia/Shanghai",
    )
    now = datetime(2026, 9, 14, 9, 7, 30, tzinfo=SHANGHAI)

    assert trigger.next_fire_time(None, now) == datetime(
        2026, 9, 14, 9, 15, tzinfo=SHANGHAI
    )
    assert trigger.next_fire_time(
        datetime(2026, 9, 14, 9, 15, tzinfo=SHANGHAI),
        now,
    ) == datetime(2026, 9, 14, 9, 30, tzinfo=SHANGHAI)


def test_cron_trigger_matches_timezone_and_weekend_boundary() -> None:
    trigger = CronTrigger(
        minute="0",
        hour="9",
        day="*",
        month="*",
        day_of_week="1-5",
        timezone="Asia/Shanghai",
    )
    friday_after_close = datetime(2026, 9, 11, 10, 0, tzinfo=SHANGHAI)

    assert trigger.next_fire_time(None, friday_after_close) == datetime(
        2026, 9, 14, 9, 0, tzinfo=SHANGHAI
    )
    utc_now = datetime(2026, 9, 14, 0, 59, tzinfo=UTC)
    assert trigger.next_fire_time(None, utc_now) == datetime(
        2026, 9, 14, 9, 0, tzinfo=SHANGHAI
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CronTrigger(minute="60"),
        lambda: CronTrigger(hour="24"),
        lambda: CronTrigger(day="0"),
        lambda: CronTrigger(month="13"),
        lambda: CronTrigger(day_of_week="8"),
        lambda: CronTrigger(minute="*/0"),
        lambda: CronTrigger(minute="10-1"),
    ],
)
def test_cron_trigger_rejects_invalid_expressions(factory) -> None:
    with pytest.raises(ValueError):
        factory()


def test_interval_trigger_anchors_to_start_time_and_skips_stale_intervals() -> None:
    trigger = IntervalTrigger(
        seconds=300,
        start_at=datetime(2026, 9, 15, 9, 0, tzinfo=SHANGHAI),
        timezone="Asia/Shanghai",
    )
    now = datetime(2026, 9, 15, 9, 12, tzinfo=SHANGHAI)

    assert trigger.next_fire_time(None, now) == datetime(
        2026, 9, 15, 9, 15, tzinfo=SHANGHAI
    )
    assert trigger.next_fire_time(
        datetime(2026, 9, 15, 9, 10, tzinfo=SHANGHAI),
        now,
    ) == datetime(2026, 9, 15, 9, 15, tzinfo=SHANGHAI)


def test_date_trigger_fires_once() -> None:
    run_at = datetime(2026, 9, 15, 15, 5, tzinfo=SHANGHAI)
    trigger = DateTrigger(run_at)

    assert trigger.next_fire_time(
        None,
        datetime(2026, 9, 15, 15, 0, tzinfo=SHANGHAI),
    ) == run_at
    assert trigger.next_fire_time(run_at, run_at) is None
    assert trigger.next_fire_time(
        None,
        datetime(2026, 9, 15, 15, 6, tzinfo=SHANGHAI),
    ) is None


def test_fixed_delay_trigger_uses_previous_completion_or_fire_time() -> None:
    trigger = FixedDelayTrigger(timedelta(seconds=90), timezone="UTC")
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)

    assert trigger.next_fire_time(None, now) == datetime(
        2026, 9, 15, 9, 1, 30, tzinfo=UTC
    )
    assert trigger.next_fire_time(
        datetime(2026, 9, 15, 9, 0, 30, tzinfo=UTC),
        now,
    ) == datetime(2026, 9, 15, 9, 2, tzinfo=UTC)


def test_trading_calendar_handles_sessions_holidays_and_weekend_boundaries() -> None:
    calendar = TradingCalendar(holidays={"SSE": [date(2026, 9, 14)]})

    assert calendar.is_trading_day("SZSE", date(2026, 9, 11))
    assert not calendar.is_trading_day("SZSE", date(2026, 9, 13))
    assert not calendar.is_trading_day("sse", date(2026, 9, 14))
    assert calendar.get_sessions("BSE", date(2026, 9, 13)) == []
    assert [
        (session.start, session.end)
        for session in calendar.get_sessions("BSE", date(2026, 9, 15))
    ] == [
        (time(9, 30), time(11, 30)),
        (time(13, 0), time(15, 0)),
    ]
    assert calendar.next_trading_day("SSE", date(2026, 9, 11)) == date(2026, 9, 15)
    assert calendar.previous_trading_day(
        "SSE", date(2026, 9, 14)
    ) == date(2026, 9, 11)


def test_trading_calendar_rejects_unknown_market() -> None:
    with pytest.raises(ValueError, match="unsupported market"):
        TradingCalendar().is_trading_day("NYSE", date(2026, 9, 15))

from __future__ import annotations

from xquant.storage import PostgresStore

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS scheduler;

CREATE TABLE IF NOT EXISTS scheduler.scheduler_task (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    handler TEXT NOT NULL,
    description TEXT,
    queue TEXT NOT NULL DEFAULT 'default',
    priority INTEGER NOT NULL DEFAULT 5,
    timeout_seconds INTEGER NOT NULL DEFAULT 300,
    retry_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    concurrency_policy TEXT NOT NULL DEFAULT 'FORBID',
    rate_limit_key TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduler_task_enabled
    ON scheduler.scheduler_task (enabled);
CREATE INDEX IF NOT EXISTS idx_scheduler_task_queue_priority
    ON scheduler.scheduler_task (queue, priority);

CREATE TABLE IF NOT EXISTS scheduler.scheduler_schedule (
    id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    calendar TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    misfire_policy TEXT NOT NULL DEFAULT 'FIRE_ONCE',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    max_catch_up_runs INTEGER NOT NULL DEFAULT 30,
    last_fire_at TIMESTAMPTZ,
    next_fire_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduler_schedule_task_name
    ON scheduler.scheduler_schedule (task_name);
CREATE INDEX IF NOT EXISTS idx_scheduler_schedule_enabled
    ON scheduler.scheduler_schedule (enabled);
CREATE INDEX IF NOT EXISTS idx_scheduler_schedule_next_fire_at
    ON scheduler.scheduler_schedule (next_fire_at);

CREATE TABLE IF NOT EXISTS scheduler.scheduler_execution (
    id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    schedule_id TEXT,
    parent_execution_id TEXT,
    queue TEXT NOT NULL DEFAULT 'default',
    priority INTEGER NOT NULL DEFAULT 5,
    status TEXT NOT NULL,
    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    scheduled_at TIMESTAMPTZ,
    queued_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    attempt INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    worker_id TEXT,
    trace_id TEXT NOT NULL DEFAULT '',
    result_json JSONB,
    error_type TEXT,
    error_message TEXT,
    duration_ms DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduler_execution_task_name
    ON scheduler.scheduler_execution (task_name);
CREATE INDEX IF NOT EXISTS idx_scheduler_execution_schedule_id
    ON scheduler.scheduler_execution (schedule_id);
CREATE INDEX IF NOT EXISTS idx_scheduler_execution_status
    ON scheduler.scheduler_execution (status);
CREATE INDEX IF NOT EXISTS idx_scheduler_execution_scheduled_at
    ON scheduler.scheduler_execution (scheduled_at);
CREATE INDEX IF NOT EXISTS idx_scheduler_execution_started_at
    ON scheduler.scheduler_execution (started_at);
CREATE INDEX IF NOT EXISTS idx_scheduler_execution_trace_id
    ON scheduler.scheduler_execution (trace_id);
CREATE INDEX IF NOT EXISTS idx_scheduler_execution_stale_running
    ON scheduler.scheduler_execution (
        COALESCE(started_at, queued_at, scheduled_at, created_at)
    )
    WHERE status IN ('RUNNING', 'RETRYING');

CREATE TABLE IF NOT EXISTS scheduler.scheduler_execution_log (
    id BIGSERIAL PRIMARY KEY,
    execution_id TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduler_execution_log_execution_id
    ON scheduler.scheduler_execution_log (execution_id, created_at);
"""


def ensure_scheduler_schema(store: PostgresStore) -> None:
    """Create scheduler tables and indexes without using an ORM or migration hook."""

    store.execute(SCHEMA_SQL)


__all__ = ["SCHEMA_SQL", "ensure_scheduler_schema"]

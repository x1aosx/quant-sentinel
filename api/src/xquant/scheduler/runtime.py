from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xquant.storage import StorageSettings

from .application.recovery_service import RecoveryService
from .application.scheduler_service import SchedulerService
from .application.task_executor import TaskExecutor
from .application.task_registry import TaskRegistry, default_registry
from .dispatcher import LocalDispatcher, TaskDispatcher
from .engine import APSchedulerEngine, MemorySchedulerEngine, SchedulerEngine
from .lock import MemoryLockManager, RedisLockManager
from .repository import (
    ExecutionRepository,
    InMemoryExecutionRepository,
    InMemoryScheduleRepository,
    InMemoryTaskRepository,
    PostgresExecutionRepository,
    PostgresScheduleRepository,
    PostgresTaskRepository,
    ScheduleRepository,
    TaskRepository,
    ensure_scheduler_schema,
)
from .worker import Worker, WorkerConfig, WorkerHeartbeat


@dataclass(slots=True)
class SchedulerRuntime:
    """Own all scheduler collaborators used by one process role."""

    service: SchedulerService
    engine: SchedulerEngine
    dispatcher: TaskDispatcher
    executor: TaskExecutor
    worker: Worker | None = None
    recovery: RecoveryService | None = None
    heartbeat: WorkerHeartbeat | None = None
    redis_client: Any | None = None
    graceful_shutdown_timeout: float = 60.0
    _started_engine: bool = False
    _started_worker: bool = False
    _started_recovery: bool = False

    async def start(
        self,
        *,
        start_engine: bool,
        start_worker: bool = False,
        start_recovery: bool = True,
    ) -> None:
        await self.service.sync_registry()
        if start_worker:
            if self.worker is None:
                raise RuntimeError("worker runtime is not configured")
            await self.worker.start()
            self._started_worker = True
        if start_engine:
            await self.engine.start()
            self._started_engine = True
        if start_recovery and self.recovery is not None:
            await self.recovery.start()
            self._started_recovery = True

    async def shutdown(self) -> None:
        if self._started_recovery and self.recovery is not None:
            await self.recovery.stop()
            self._started_recovery = False
        if self._started_engine:
            await self.engine.shutdown()
            self._started_engine = False
        if self._started_worker and self.worker is not None:
            await self.worker.stop()
            self._started_worker = False
        shutdown = self.dispatcher.shutdown
        try:
            await shutdown(
                wait=True,
                cancel=False,
                timeout_seconds=self.graceful_shutdown_timeout,
            )
        except TypeError:
            await shutdown(wait=True, cancel=False)
        if self.redis_client is not None:
            await self.redis_client.aclose()
            self.redis_client = None


def build_scheduler_runtime(
    db: Any,
    settings: StorageSettings,
    *,
    registry: TaskRegistry | None = None,
) -> SchedulerRuntime | None:
    """Build a scheduler runtime when explicitly enabled by configuration."""

    config = settings.scheduler
    if not config.enabled:
        return None

    task_registry = registry or default_registry()
    task_repository: TaskRepository
    schedule_repository: ScheduleRepository
    execution_repository: ExecutionRepository
    redis_client: Any | None = None

    if settings.storage_backend == "postgres" and hasattr(db, "postgres"):
        if settings.auto_migrate:
            ensure_scheduler_schema(db.postgres)
        task_repository = PostgresTaskRepository(db.postgres)
        schedule_repository = PostgresScheduleRepository(db.postgres)
        execution_repository = PostgresExecutionRepository(db.postgres)
    else:
        task_repository = InMemoryTaskRepository()
        schedule_repository = InMemoryScheduleRepository()
        execution_repository = InMemoryExecutionRepository()

    if config.dispatcher_type == "redis":
        import redis.asyncio as redis

        worker_id = _runtime_worker_id()
        redis_client = redis.Redis.from_url(
            settings.redis.url,
            decode_responses=False,
            socket_timeout=settings.redis.socket_timeout_seconds,
        )
        from .dispatcher.redis import RedisDispatcher

        dispatcher: TaskDispatcher = RedisDispatcher(
            redis_client,
            queue_prefix=config.queue_prefix,
            lease_seconds=config.lease_seconds,
        )
        lock_manager: Any = RedisLockManager(
            redis_client,
            key_prefix=config.lock_prefix,
        )
        heartbeat = WorkerHeartbeat(
            redis_client,
            worker_id=worker_id,
            queue_prefix=config.queue_prefix,
            ttl_seconds=config.heartbeat_timeout_seconds,
            interval_seconds=config.heartbeat_interval_seconds,
        )
    else:
        lock_manager = MemoryLockManager()
        heartbeat = None

    executor = TaskExecutor(
        task_registry,
        execution_repository,
        lock_manager=lock_manager,
        worker_id=None if config.dispatcher_type == "redis" else "local-scheduler",
    )
    if config.dispatcher_type == "local":
        dispatcher = LocalDispatcher(executor)

    service = SchedulerService(
        task_registry,
        task_repository,
        schedule_repository,
        execution_repository,
        dispatcher,
        max_catch_up_runs=config.max_catch_up_runs,
    )
    engine: SchedulerEngine
    if config.engine_type == "memory":
        engine = MemorySchedulerEngine(service)
    else:
        engine = APSchedulerEngine(service)

    worker: Worker | None = None
    recovery: RecoveryService | None = None
    if config.dispatcher_type == "redis":
        worker = Worker(
            dispatcher,
            executor,
            config=WorkerConfig(
                queues=tuple(config.worker_queues),
                concurrency=config.worker_concurrency,
                graceful_shutdown_timeout=config.graceful_shutdown_timeout_seconds,
            ),
            heartbeat=heartbeat,
            repository=execution_repository,
            worker_id=worker_id,
        )
    if config.recovery_enabled:
        recovery = RecoveryService(
            execution_repository,
            dispatcher=dispatcher,
            task_repository=task_repository,
            heartbeat=heartbeat,
            queues=config.worker_queues,
            interval_seconds=config.recovery_interval_seconds,
            stale_after_seconds=config.heartbeat_timeout_seconds,
        )

    return SchedulerRuntime(
        service=service,
        engine=engine,
        dispatcher=dispatcher,
        executor=executor,
        worker=worker,
        recovery=recovery,
        heartbeat=heartbeat,
        redis_client=redis_client,
        graceful_shutdown_timeout=config.graceful_shutdown_timeout_seconds,
    )


def _runtime_worker_id() -> str:
    import os
    import socket

    return f"{socket.gethostname()}-{os.getpid()}"


__all__ = ["SchedulerRuntime", "build_scheduler_runtime"]

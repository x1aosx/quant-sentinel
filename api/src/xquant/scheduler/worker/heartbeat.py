from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from redis.asyncio import Redis


class HeartbeatStore(Protocol):
    async def register(
        self,
        *,
        queues: Sequence[str],
        running_tasks: int = 0,
        status: str = "idle",
    ) -> None: ...

    async def renew(self) -> bool: ...

    async def unregister(self) -> None: ...

    def set_running_tasks(self, running_tasks: int) -> None: ...


class WorkerHeartbeat:
    """Redis-backed worker registration, renewal, and removal."""

    def __init__(
        self,
        client: Redis,
        *,
        worker_id: str,
        queue_prefix: str = "xqs:scheduler",
        ttl_seconds: float = 60.0,
        interval_seconds: float = 10.0,
        hostname: str | None = None,
        pid: int | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id cannot be empty")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.client = client
        self.worker_id = worker_id
        self.queue_prefix = queue_prefix.rstrip(":")
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        self.hostname = hostname
        self.pid = pid
        self._now = now or (lambda: datetime.now(UTC))
        self._started_at: datetime | None = None
        self._queues: tuple[str, ...] = ()
        self._running_tasks = 0
        self._status = "idle"
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def key(self) -> str:
        prefix = f"{self.queue_prefix}:" if self.queue_prefix else ""
        return f"{prefix}worker:{self.worker_id}"

    async def register(
        self,
        *,
        queues: Sequence[str],
        running_tasks: int = 0,
        status: str = "idle",
    ) -> None:
        self._started_at = self._started_at or self._now()
        self._queues = tuple(queues)
        self._running_tasks = running_tasks
        self._status = status
        await self.client.set(
            self.key,
            self._payload(queues),
            ex=_redis_ttl(self.ttl_seconds),
        )

    async def renew(self) -> bool:
        result = await self.client.set(
            self.key,
            self._payload(self._queues),
            ex=_redis_ttl(self.ttl_seconds),
            xx=True,
        )
        return bool(result)

    async def unregister(self) -> None:
        await self.client.delete(self.key)

    async def start(self, *, queues: Sequence[str]) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        await self.register(queues=queues, status="running")
        self._task = asyncio.create_task(
            self._heartbeat_loop(tuple(queues)),
            name=f"scheduler-heartbeat:{self.worker_id}",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await self.unregister()

    def set_running_tasks(self, running_tasks: int) -> None:
        self._running_tasks = max(0, int(running_tasks))

    async def is_alive(self, worker_id: str) -> bool:
        return bool(await self.client.exists(self._worker_key(worker_id)))

    async def live_worker_ids(self) -> set[str]:
        keys = await self.client.keys(self._worker_pattern())
        return {_as_text(key).rsplit(":", 1)[-1] for key in keys if _as_text(key)}

    async def _heartbeat_loop(self, queues: tuple[str, ...]) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
                continue
            except TimeoutError:
                pass
            renewed = await self.renew()
            if not renewed:
                await self.register(
                    queues=queues,
                    running_tasks=self._running_tasks,
                    status="running",
                )

    def _payload(self, queues: Sequence[str]) -> str:
        payload: dict[str, Any] = {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "pid": self.pid,
            "queues": list(queues),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_heartbeat": self._now().isoformat(),
            "status": self._status,
            "running_tasks": self._running_tasks,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _worker_key(self, worker_id: str) -> str:
        prefix = f"{self.queue_prefix}:" if self.queue_prefix else ""
        return f"{prefix}worker:{worker_id}"

    def _worker_pattern(self) -> str:
        prefix = f"{self.queue_prefix}:" if self.queue_prefix else ""
        return f"{prefix}worker:*"


RedisHeartbeat = WorkerHeartbeat


def _redis_ttl(ttl_seconds: float) -> int:
    return max(1, int(math_ceil(ttl_seconds)))


def math_ceil(value: float) -> int:
    import math

    return math.ceil(value)


def _as_text(value: str | bytes) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


__all__ = [
    "HeartbeatStore",
    "RedisHeartbeat",
    "WorkerHeartbeat",
]

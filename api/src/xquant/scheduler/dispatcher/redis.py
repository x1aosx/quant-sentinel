from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from xquant.scheduler.domain import TaskExecution, TaskResult

from .base import TaskDispatcher

if TYPE_CHECKING:
    from redis.asyncio import Redis


_SCORE_MULTIPLIER = 1_000_000_000_000

# Redis Lua keeps promotion and claim atomic. KEYS are delayed, ready, sequence,
# inflight. ARGV are now, promotion limit, default priority, and lease deadline.
_PROMOTE_AND_CLAIM_SCRIPT = """
-- PROMOTE_AND_CLAIM
local due = redis.call(
    'zrangebyscore', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2])
)
for _, member in ipairs(due) do
    if redis.call('zrem', KEYS[1], member) == 1 then
        local priority = tonumber(ARGV[3])
        local decoded_ok, decoded = pcall(cjson.decode, member)
        if decoded_ok and type(decoded) == 'table' and decoded['priority'] ~= nil then
            priority = tonumber(decoded['priority'])
        end
        local sequence = redis.call('incr', KEYS[3])
        redis.call(
            'zadd',
            KEYS[2],
            priority * 1000000000000 + sequence,
            member
        )
    end
end

local popped = redis.call('zpopmin', KEYS[2], 1)
if #popped == 0 then
    return nil
end

local member = popped[1]
redis.call('zadd', KEYS[4], ARGV[4], member)
return member
"""

# KEYS are ready, inflight, sequence. ARGV are now, limit, default priority.
_RECOVER_EXPIRED_SCRIPT = """
-- RECOVER_EXPIRED
local expired = redis.call(
    'zrangebyscore', KEYS[2], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2])
)
local recovered = 0
for _, member in ipairs(expired) do
    if redis.call('zrem', KEYS[2], member) == 1 then
        local priority = tonumber(ARGV[3])
        local decoded_ok, decoded = pcall(cjson.decode, member)
        if decoded_ok and type(decoded) == 'table' and decoded['priority'] ~= nil then
            priority = tonumber(decoded['priority'])
        end
        local sequence = redis.call('incr', KEYS[3])
        redis.call(
            'zadd',
            KEYS[1],
            priority * 1000000000000 + sequence,
            member
        )
        recovered = recovered + 1
    end
end
return recovered
"""


@dataclass(frozen=True, slots=True)
class RedisDelivery:
    """An unacknowledged queue message leased to one worker."""

    queue: str
    message: str
    worker_id: str

    @property
    def execution_id(self) -> str | None:
        try:
            return str(json.loads(self.message)["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


class RedisDispatcher(TaskDispatcher):
    """Reliable Redis queue with priority, delay, leases, and explicit ack.

    Each queue uses a ready sorted set, a delayed sorted set, an inflight
    sorted set scored by lease deadline, and a wake list. The Lua claim script
    atomically promotes due messages, removes the highest-priority ready
    message, and records its lease. Ack removes the lease. Recover returns
    expired leases to the ready set, giving at-least-once delivery.
    """

    def __init__(
        self,
        client: Redis,
        *,
        queue_prefix: str = "xqs:scheduler",
        lease_seconds: float = 60.0,
        promotion_batch_size: int = 100,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if promotion_batch_size <= 0:
            raise ValueError("promotion_batch_size must be positive")
        self.client = client
        self.queue_prefix = queue_prefix.rstrip(":")
        self.lease_seconds = lease_seconds
        self.promotion_batch_size = promotion_batch_size
        self._now = now or (lambda: datetime.now(UTC))

    async def dispatch(self, execution: TaskExecution) -> None:
        message = serialize_execution(execution)
        await self._enqueue(
            queue=execution.queue,
            message=message,
            priority=execution.priority,
            scheduled_at=execution.scheduled_at,
        )

    async def dispatch_raw(
        self,
        message: Mapping[str, Any] | str | bytes,
        *,
        queue: str | None = None,
        priority: int | None = None,
        scheduled_at: datetime | None = None,
    ) -> str:
        """Dispatch a pre-serialized message while preserving JSON semantics."""

        normalized = _normalize_message(message)
        payload = _message_payload(normalized)
        if payload is not None:
            queue = queue or _optional_string(payload.get("queue"))
            if priority is None and payload.get("priority") is not None:
                priority = int(payload["priority"])
            if scheduled_at is None:
                scheduled_at = _optional_datetime(payload.get("scheduled_at"))
            payload["queue"] = queue or "default"
            payload["priority"] = 5 if priority is None else int(priority)
            if scheduled_at is not None:
                payload["scheduled_at"] = scheduled_at.isoformat()
            normalized = _dumps(payload)

        queue = queue or "default"
        priority = 5 if priority is None else int(priority)
        if not 0 <= priority <= 9:
            raise ValueError("priority must be between 0 and 9")
        await self._enqueue(
            queue=queue,
            message=normalized,
            priority=priority,
            scheduled_at=scheduled_at,
        )
        return normalized

    async def receive(
        self,
        queue: str,
        *,
        worker_id: str,
        timeout: float = 0.0,
    ) -> RedisDelivery | None:
        return await self.receive_any(
            (queue,),
            worker_id=worker_id,
            timeout=timeout,
        )

    async def receive_any(
        self,
        queues: Sequence[str],
        *,
        worker_id: str,
        timeout: float = 0.0,
    ) -> RedisDelivery | None:
        normalized_queues = tuple(dict.fromkeys(queue for queue in queues if queue))
        if not normalized_queues:
            raise ValueError("at least one queue is required")
        if not worker_id:
            raise ValueError("worker_id cannot be empty")

        if timeout <= 0:
            for queue in normalized_queues:
                delivery = await self._claim_once(queue, worker_id=worker_id)
                if delivery is not None:
                    return delivery
            return None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for queue in normalized_queues:
                delivery = await self._claim_once(queue, worker_id=worker_id)
                if delivery is not None:
                    return delivery

            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            wait_seconds = await self._bounded_wait_seconds(
                normalized_queues,
                remaining,
            )
            if wait_seconds <= 0:
                continue
            await self.client.blpop(
                [self._global_wake_key()],
                timeout=wait_seconds,
            )

    async def ack(
        self,
        delivery: RedisDelivery | str,
        message: str | None = None,
    ) -> bool:
        queue, raw_message = _delivery_parts(delivery, message)
        result = await self.client.zrem(self._inflight_key(queue), raw_message)
        return bool(result)

    async def requeue(
        self,
        delivery: RedisDelivery | str,
        message: str | None = None,
        *,
        delay_seconds: float = 0.0,
        priority: int | None = None,
    ) -> None:
        queue, raw_message = _delivery_parts(delivery, message)
        await self.ack(queue, raw_message)
        payload = _message_payload(raw_message)
        if priority is None:
            priority = int(payload.get("priority", 5)) if payload else 5
        scheduled_at = (
            self._now() if delay_seconds <= 0 else (self._now() + _seconds(delay_seconds))
        )
        await self._enqueue(
            queue=queue,
            message=raw_message,
            priority=priority,
            scheduled_at=scheduled_at,
        )

    async def recover(
        self,
        queue: str,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        if limit is None:
            limit = self.promotion_batch_size
        if limit <= 0:
            raise ValueError("limit must be positive")
        current = now or self._now()
        recovered = await self.client.eval(
            _RECOVER_EXPIRED_SCRIPT,
            3,
            self._ready_key(queue),
            self._inflight_key(queue),
            self._sequence_key(queue),
            _timestamp(current),
            limit,
            5,
        )
        if int(recovered or 0):
            await self._wake(queue)
        return int(recovered or 0)

    async def recover_all(
        self,
        queues: Sequence[str],
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        return sum(
            [await self.recover(queue, now=now, limit=limit) for queue in dict.fromkeys(queues)]
        )

    def decode_execution(self, delivery: RedisDelivery | str | bytes) -> TaskExecution:
        raw = delivery.message if isinstance(delivery, RedisDelivery) else delivery
        return deserialize_execution(raw)

    async def shutdown(self, *, wait: bool = True, cancel: bool = False) -> None:
        return None

    async def _claim_once(
        self,
        queue: str,
        *,
        worker_id: str,
    ) -> RedisDelivery | None:
        current = self._now()
        member = await self.client.eval(
            _PROMOTE_AND_CLAIM_SCRIPT,
            4,
            self._delayed_key(queue),
            self._ready_key(queue),
            self._sequence_key(queue),
            self._inflight_key(queue),
            _timestamp(current),
            self.promotion_batch_size,
            5,
            _timestamp(current) + self.lease_seconds,
        )
        if member is None:
            return None
        raw = _as_text(member)
        await self.client.lpop(self._global_wake_key())
        return RedisDelivery(queue=queue, message=raw, worker_id=worker_id)

    async def _enqueue(
        self,
        *,
        queue: str,
        message: str,
        priority: int,
        scheduled_at: datetime | None,
    ) -> None:
        if not queue:
            raise ValueError("queue cannot be empty")
        if not 0 <= priority <= 9:
            raise ValueError("priority must be between 0 and 9")
        current = self._now()
        if scheduled_at is not None and _is_future(scheduled_at, current):
            await self.client.zadd(
                self._delayed_key(queue),
                {message: _timestamp(scheduled_at)},
            )
        else:
            sequence = await self.client.incr(self._sequence_key(queue))
            await self.client.zadd(
                self._ready_key(queue),
                {message: priority * _SCORE_MULTIPLIER + int(sequence)},
            )
        await self._wake(queue)

    async def _wake(self, queue: str) -> None:
        await self.client.rpush(self._global_wake_key(), queue)

    async def _bounded_wait_seconds(
        self,
        queues: Sequence[str],
        remaining: float,
    ) -> float:
        wait_seconds = remaining
        current = self._now()
        for queue in queues:
            next_due = await self.client.zrange(
                self._delayed_key(queue),
                0,
                0,
                withscores=True,
            )
            if not next_due:
                continue
            due_at = float(next_due[0][1])
            wait_seconds = min(
                wait_seconds,
                max(0.0, due_at - _timestamp(current)),
            )
        return wait_seconds

    def _base_key(self, queue: str) -> str:
        prefix = f"{self.queue_prefix}:" if self.queue_prefix else ""
        return f"{prefix}queue:{queue}"

    def _ready_key(self, queue: str) -> str:
        return f"{self._base_key(queue)}:ready"

    def _delayed_key(self, queue: str) -> str:
        return f"{self._base_key(queue)}:delayed"

    def _inflight_key(self, queue: str) -> str:
        return f"{self._base_key(queue)}:inflight"

    def _sequence_key(self, queue: str) -> str:
        return f"{self._base_key(queue)}:sequence"

    def _global_wake_key(self) -> str:
        prefix = f"{self.queue_prefix}:" if self.queue_prefix else ""
        return f"{prefix}wake"


def serialize_execution(execution: TaskExecution) -> str:
    return _dumps(asdict(execution))


def deserialize_execution(message: str | bytes) -> TaskExecution:
    payload = _json_loads(message)
    if not isinstance(payload, Mapping):
        raise TypeError("execution message must decode to a JSON object")
    if "execution" in payload and isinstance(payload["execution"], Mapping):
        payload = payload["execution"]
    data = dict(payload)
    for field_name in (
        "scheduled_at",
        "queued_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ):
        data[field_name] = _optional_datetime(data.get(field_name))
    result = data.get("result")
    if isinstance(result, Mapping):
        data["result"] = TaskResult(**dict(result))
    data["params"] = dict(data.get("params") or {})
    return TaskExecution(**data)


def _normalize_message(message: Mapping[str, Any] | str | bytes) -> str:
    if isinstance(message, (str, bytes)):
        return _as_text(message)
    return _dumps(message)


def _message_payload(message: str | bytes) -> dict[str, Any] | None:
    try:
        payload = _json_loads(message)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_loads(value: str | bytes) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)


def _as_text(value: str | bytes) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _optional_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.timestamp()


def _is_future(value: datetime, now: datetime) -> bool:
    return _timestamp(value) > _timestamp(now)


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


def _delivery_parts(
    delivery: RedisDelivery | str,
    message: str | None,
) -> tuple[str, str]:
    if isinstance(delivery, RedisDelivery):
        if message is not None:
            raise ValueError("message must not be provided with a RedisDelivery")
        return delivery.queue, delivery.message
    if message is None:
        raise ValueError("message is required when delivery is a queue name")
    return delivery, message


__all__ = [
    "RedisDelivery",
    "RedisDispatcher",
    "deserialize_execution",
    "serialize_execution",
]

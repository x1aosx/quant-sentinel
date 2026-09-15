from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .base import RateLimiter

if TYPE_CHECKING:
    from redis.asyncio import Redis


_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local values = redis.call('HMGET', key, 'tokens', 'timestamp')
local tokens = tonumber(values[1])
local timestamp = tonumber(values[2])
if tokens == nil then
    tokens = capacity
    timestamp = now_ms
end
local elapsed = math.max(0, now_ms - timestamp) / 1000.0
tokens = math.min(capacity, tokens + elapsed * refill_rate)
if tokens >= requested then
    tokens = tokens - requested
    redis.call('HSET', key, 'tokens', tokens, 'timestamp', now_ms)
    redis.call('PEXPIRE', key, math.ceil(capacity / refill_rate * 1000) + 1000)
    return 0
end
redis.call('HSET', key, 'tokens', tokens, 'timestamp', now_ms)
redis.call('PEXPIRE', key, math.ceil(capacity / refill_rate * 1000) + 1000)
return math.ceil((requested - tokens) / refill_rate * 1000)
"""


class RedisRateLimiter(RateLimiter):
    """Redis token bucket shared by all workers using the same Redis instance."""

    def __init__(
        self,
        client: Redis,
        *,
        capacity: float,
        refill_rate: float,
        key_prefix: str = "scheduler:rate_limit",
        poll_interval_seconds: float = 0.05,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative")
        self.client = client
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.key_prefix = key_prefix.rstrip(":")
        self.poll_interval_seconds = poll_interval_seconds
        self._sleep = sleep or asyncio.sleep

    async def acquire(self, key: str, tokens: int = 1) -> None:
        if not key:
            raise ValueError("rate limit key cannot be empty")
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self.capacity:
            raise ValueError("tokens cannot exceed capacity")

        while True:
            wait_ms = await self.client.eval(
                _ACQUIRE_SCRIPT,
                1,
                self._key(key),
                self.capacity,
                self.refill_rate,
                tokens,
            )
            wait_seconds = max(0.0, float(wait_ms) / 1000)
            if wait_seconds == 0:
                return
            await self._sleep(max(wait_seconds, self.poll_interval_seconds))

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}" if self.key_prefix else key


__all__ = ["RedisRateLimiter"]

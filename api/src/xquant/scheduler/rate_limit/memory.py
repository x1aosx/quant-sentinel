from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .base import RateLimiter


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class MemoryRateLimiter(RateLimiter):
    """Process-local token bucket with per-key state."""

    def __init__(
        self,
        *,
        rate: float,
        period: float = 1.0,
        capacity: float | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if period <= 0:
            raise ValueError("period must be positive")
        self.rate_per_second = rate / period
        self.capacity = capacity if capacity is not None else max(1.0, rate)
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._buckets: dict[str, _Bucket] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, key: str, tokens: int = 1) -> None:
        if not key:
            raise ValueError("rate limit key cannot be empty")
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        if tokens > self.capacity:
            raise ValueError("tokens cannot exceed capacity")

        while True:
            async with self._guard:
                now = self._clock()
                bucket = self._buckets.get(key)
                if bucket is None:
                    bucket = _Bucket(tokens=self.capacity, updated_at=now)
                    self._buckets[key] = bucket

                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(
                    self.capacity,
                    bucket.tokens + elapsed * self.rate_per_second,
                )
                bucket.updated_at = now
                if bucket.tokens >= tokens:
                    bucket.tokens -= tokens
                    return
                wait_seconds = (
                    tokens - bucket.tokens
                ) / self.rate_per_second
            await self._sleep(max(wait_seconds, 0.0))


InMemoryRateLimiter = MemoryRateLimiter


__all__ = ["InMemoryRateLimiter", "MemoryRateLimiter"]

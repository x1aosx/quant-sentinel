from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from .base import LockManager


@dataclass(slots=True)
class _LockEntry:
    owner_token: str
    expires_at: float


class MemoryLockManager(LockManager):
    """Process-local lock manager used by local execution and tests."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._locks: dict[str, _LockEntry] = {}
        self._guard = asyncio.Lock()

    async def acquire(
        self,
        key: str,
        ttl_seconds: int,
        owner_token: str,
    ) -> bool:
        _validate_lock_args(key, ttl_seconds, owner_token)
        now = self._clock()
        async with self._guard:
            current = self._locks.get(key)
            if current is not None and current.expires_at > now:
                return False
            self._locks[key] = _LockEntry(
                owner_token=owner_token,
                expires_at=now + ttl_seconds,
            )
            return True

    async def release(self, key: str, owner_token: str) -> bool:
        _validate_lock_args(key, 1, owner_token)
        async with self._guard:
            current = self._locks.get(key)
            if current is None or current.owner_token != owner_token:
                return False
            if current.expires_at <= self._clock():
                self._locks.pop(key, None)
                return False
            del self._locks[key]
            return True


InMemoryLockManager = MemoryLockManager


def _validate_lock_args(key: str, ttl_seconds: int, owner_token: str) -> None:
    if not key:
        raise ValueError("lock key cannot be empty")
    if ttl_seconds <= 0:
        raise ValueError("lock ttl_seconds must be positive")
    if not owner_token:
        raise ValueError("lock owner_token cannot be empty")


__all__ = ["InMemoryLockManager", "MemoryLockManager"]

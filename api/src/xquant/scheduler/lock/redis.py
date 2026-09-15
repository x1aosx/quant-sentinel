from __future__ import annotations

from typing import TYPE_CHECKING

from .base import LockManager

if TYPE_CHECKING:
    from redis.asyncio import Redis


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisLockManager(LockManager):
    """Redis-backed lock using SET NX EX and compare-and-delete release."""

    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "scheduler:lock",
    ) -> None:
        self.client = client
        self.key_prefix = key_prefix.rstrip(":")

    async def acquire(
        self,
        key: str,
        ttl_seconds: int,
        owner_token: str,
    ) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("lock ttl_seconds must be positive")
        if not key or not owner_token:
            raise ValueError("lock key and owner_token cannot be empty")
        result = await self.client.set(
            self._key(key),
            owner_token,
            nx=True,
            ex=ttl_seconds,
        )
        return bool(result)

    async def release(self, key: str, owner_token: str) -> bool:
        if not key or not owner_token:
            raise ValueError("lock key and owner_token cannot be empty")
        result = await self.client.eval(
            _RELEASE_SCRIPT,
            1,
            self._key(key),
            owner_token,
        )
        return bool(result)

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}" if self.key_prefix else key


__all__ = ["RedisLockManager"]

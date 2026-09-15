from __future__ import annotations

from abc import ABC, abstractmethod


class LockManager(ABC):
    """Abstract distributed lock with owner-token-safe release."""

    @abstractmethod
    async def acquire(
        self,
        key: str,
        ttl_seconds: int,
        owner_token: str,
    ) -> bool:
        ...

    @abstractmethod
    async def release(self, key: str, owner_token: str) -> bool:
        ...


__all__ = ["LockManager"]

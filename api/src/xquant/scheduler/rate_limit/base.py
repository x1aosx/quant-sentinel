from __future__ import annotations

from abc import ABC, abstractmethod


class RateLimiter(ABC):
    """Abstract token acquisition boundary for provider-level limits."""

    @abstractmethod
    async def acquire(self, key: str, tokens: int = 1) -> None:
        ...


__all__ = ["RateLimiter"]

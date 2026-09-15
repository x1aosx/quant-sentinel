from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import CancellationManager

if TYPE_CHECKING:
    from redis.asyncio import Redis


class RedisCancellationManager(CancellationManager):
    """Redis-backed cancellation requests shared across scheduler workers."""

    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str = "scheduler:cancellation",
        ttl_seconds: int = 3600,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.client = client
        self.key_prefix = key_prefix.rstrip(":")
        self.ttl_seconds = ttl_seconds

    async def request_cancel(
        self,
        execution_id: str,
        reason: str | None = None,
    ) -> bool:
        _validate_execution_id(execution_id)
        payload = json.dumps(
            {"reason": reason},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = await self.client.set(
            self._key(execution_id),
            payload,
            nx=True,
            ex=self.ttl_seconds,
        )
        return bool(result)

    async def is_cancelled(self, execution_id: str) -> bool:
        _validate_execution_id(execution_id)
        return bool(await self.client.exists(self._key(execution_id)))

    async def get_reason(self, execution_id: str) -> str | None:
        _validate_execution_id(execution_id)
        raw = await self.client.get(self._key(execution_id))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        reason = payload.get("reason")
        return reason if isinstance(reason, str) else None

    async def clear(self, execution_id: str) -> None:
        _validate_execution_id(execution_id)
        await self.client.delete(self._key(execution_id))

    def _key(self, execution_id: str) -> str:
        if not self.key_prefix:
            return execution_id
        return f"{self.key_prefix}:{execution_id}"


def _validate_execution_id(execution_id: str) -> None:
    if not execution_id:
        raise ValueError("execution_id cannot be empty")


__all__ = ["RedisCancellationManager"]

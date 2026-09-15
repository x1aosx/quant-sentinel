from __future__ import annotations

import asyncio

import pytest

from xquant.scheduler.cancellation import (
    MemoryCancellationManager,
    RedisCancellationManager,
)


class FakeAsyncRedis:
    def __init__(self, *, clock=None) -> None:
        self.values: dict[str, str] = {}
        self.expires_at: dict[str, float | None] = {}
        self.clock = clock or (lambda: 0.0)

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        self._expire()
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expires_at[key] = self.clock() + ex if ex is not None else None
        return True

    async def get(self, key: str) -> str | None:
        self._expire()
        return self.values.get(key)

    async def exists(self, key: str) -> int:
        self._expire()
        return int(key in self.values)

    async def delete(self, key: str) -> int:
        self._expire()
        self.expires_at.pop(key, None)
        return int(self.values.pop(key, None) is not None)

    def _expire(self) -> None:
        now = self.clock()
        expired = [
            key
            for key, expires_at in self.expires_at.items()
            if expires_at is not None and expires_at <= now
        ]
        for key in expired:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)


def test_memory_cancellation_request_reason_and_clear() -> None:
    async def scenario() -> None:
        manager = MemoryCancellationManager()

        assert not await manager.is_cancelled("execution-1")
        assert await manager.request_cancel("execution-1", "operator request")
        assert await manager.is_cancelled("execution-1")
        assert await manager.get_reason("execution-1") == "operator request"
        assert not await manager.request_cancel("execution-1", "duplicate")
        assert await manager.get_reason("execution-1") == "operator request"

        await manager.clear("execution-1")

        assert not await manager.is_cancelled("execution-1")
        assert await manager.get_reason("execution-1") is None

    asyncio.run(scenario())


def test_memory_cancellation_concurrent_requests_are_idempotent() -> None:
    async def scenario() -> None:
        manager = MemoryCancellationManager()
        results = await asyncio.gather(
            manager.request_cancel("execution-1", "first"),
            manager.request_cancel("execution-1", "second"),
        )

        assert sorted(results) == [False, True]
        assert await manager.get_reason("execution-1") in {"first", "second"}

    asyncio.run(scenario())


def test_redis_cancellation_uses_json_nx_and_ttl() -> None:
    async def scenario() -> None:
        client = FakeAsyncRedis()
        manager = RedisCancellationManager(
            client,
            key_prefix="test:cancellation",
            ttl_seconds=45,
        )

        assert await manager.request_cancel("execution-1", "operator request")
        assert await manager.is_cancelled("execution-1")
        assert await manager.get_reason("execution-1") == "operator request"
        assert not await manager.request_cancel("execution-1", "duplicate")
        assert client.values["test:cancellation:execution-1"] == '{"reason":"operator request"}'
        assert client.expires_at["test:cancellation:execution-1"] == 45

        await manager.clear("execution-1")

        assert not await manager.is_cancelled("execution-1")
        assert "test:cancellation:execution-1" not in client.expires_at

    asyncio.run(scenario())


def test_redis_cancellation_keeps_cancelled_state_for_malformed_json() -> None:
    async def scenario() -> None:
        client = FakeAsyncRedis()
        manager = RedisCancellationManager(client)
        key = "scheduler:cancellation:execution-1"
        await client.set(key, "{broken", ex=30)

        assert await manager.is_cancelled("execution-1")
        assert await manager.get_reason("execution-1") is None

    asyncio.run(scenario())


def test_redis_cancellation_ttl_expires_with_clock() -> None:
    async def scenario() -> None:
        now = 100.0
        client = FakeAsyncRedis(clock=lambda: now)
        manager = RedisCancellationManager(
            client,
            ttl_seconds=10,
        )

        await manager.request_cancel("execution-1", "timeout")
        assert await manager.is_cancelled("execution-1")

        now = 111.0

        assert not await manager.is_cancelled("execution-1")
        assert await manager.get_reason("execution-1") is None

    asyncio.run(scenario())


def test_redis_cancellation_rejects_invalid_ttl_and_execution_id() -> None:
    client = FakeAsyncRedis()

    with pytest.raises(ValueError, match="ttl_seconds"):
        RedisCancellationManager(client, ttl_seconds=0)

    async def scenario() -> None:
        manager = RedisCancellationManager(client)
        with pytest.raises(ValueError, match="execution_id"):
            await manager.request_cancel("")

    asyncio.run(scenario())

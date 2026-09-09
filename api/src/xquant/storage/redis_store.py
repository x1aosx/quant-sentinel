from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import redis

from .settings import RedisSettings


class RedisStore:
    def __init__(self, settings: RedisSettings, client: redis.Redis | None = None) -> None:
        self.settings = settings
        self._client = client or redis.Redis.from_url(
            settings.url,
            decode_responses=True,
            socket_timeout=settings.socket_timeout_seconds,
        )

    def _key(self, key: str) -> str:
        return f"{self.settings.key_prefix}:{key}"

    def get(self, key: str) -> str | None:
        return self._client.get(self._key(key))

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Redis cache TTL must be greater than zero")
        self._client.set(self._key(key), value, ex=ttl_seconds)

    def get_json(self, key: str) -> Any | None:
        raw = self.get(key)
        return json.loads(raw) if raw is not None else None

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        self.set(key, encoded, ttl_seconds)

    def cache_get_or_set(self, key: str, factory: Callable[[], Any], ttl_seconds: int) -> Any:
        cached = self.get_json(key)
        if cached is not None:
            return cached
        value = factory()
        self.set_json(key, value, ttl_seconds)
        return value

    def delete(self, *keys: str) -> None:
        if keys:
            self._client.delete(*(self._key(key) for key in keys))

    def expire(self, key: str, ttl_seconds: int) -> None:
        self._client.expire(self._key(key), ttl_seconds)

    def publish(self, channel: str, message: Mapping[str, Any]) -> None:
        self._client.publish(self._key(channel), json.dumps(message, ensure_ascii=False, default=str))

    def stream_add(
        self,
        stream: str,
        fields: Mapping[str, Any],
        *,
        max_len: int = 10_000,
    ) -> str:
        encoded_fields = {
            str(key): str(value) if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False, default=str)
            for key, value in fields.items()
        }
        return self._client.xadd(
            self._key(stream),
            encoded_fields,
            maxlen=max_len,
            approximate=True,
        )

    def stream_read(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 10,
        block_ms: int = 1_000,
    ) -> list[tuple[str, dict[str, str]]]:
        response = self._client.xreadgroup(
            group,
            consumer,
            {self._key(stream): ">"},
            count=count,
            block=block_ms,
        )
        if not response:
            return []
        return [(message_id, dict(fields)) for _, messages in response for message_id, fields in messages]

    def stream_ack(self, stream: str, group: str, *message_ids: str) -> None:
        if message_ids:
            self._client.xack(self._key(stream), group, *message_ids)

    def health(self) -> dict[str, str]:
        self._client.ping()
        return {"status": "ok"}

    def close(self) -> None:
        self._client.close()

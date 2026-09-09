from __future__ import annotations

import pytest

from xquant.storage import RedisSettings, RedisStore


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    def delete(self, *keys: str) -> None:
        for key in keys:
            self.data.pop(key, None)


def test_redis_store_uses_prefixed_json_cache() -> None:
    fake = FakeRedis()
    store = RedisStore(RedisSettings(key_prefix="test:v1"), client=fake)

    store.set_json("dataset:1", {"name": "测试"}, ttl_seconds=60)

    assert store.get_json("dataset:1") == {"name": "测试"}
    assert "test:v1:dataset:1" in fake.data

    store.delete("dataset:1")
    assert store.get_json("dataset:1") is None


def test_redis_cache_requires_ttl() -> None:
    store = RedisStore(RedisSettings(key_prefix="test:v1"), client=FakeRedis())

    with pytest.raises(ValueError):
        store.set_json("dataset:1", {"name": "test"}, ttl_seconds=0)

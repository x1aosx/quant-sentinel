from __future__ import annotations

from pydantic import SecretStr

from xquant.storage import PostgresSettings, RedisSettings, StorageSettings


def test_postgres_url_encodes_credentials() -> None:
    settings = PostgresSettings(
        host="db.internal",
        port=5432,
        user="xqs app",
        password=SecretStr("pass word"),
        database="xquant",
    )

    assert settings.url == (
        "postgresql+psycopg://xqs+app:pass+word@db.internal:5432/xquant"
    )


def test_redis_url_encodes_credentials() -> None:
    settings = RedisSettings(
        host="cache.internal",
        port=6380,
        password=SecretStr("p@ss"),
        db=2,
    )

    assert settings.url == "redis://:p%40ss@cache.internal:6380/2"


def test_storage_backend_can_be_selected(monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")

    assert StorageSettings().storage_backend == "postgres"

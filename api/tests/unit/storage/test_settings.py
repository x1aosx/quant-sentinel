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


def test_postgres_database_uses_compose_environment_name(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DB", "quant-sentinel")
    monkeypatch.delenv("POSTGRES_DATABASE", raising=False)

    assert PostgresSettings().database == "quant-sentinel"
    assert StorageSettings().postgres.database == "quant-sentinel"


def test_postgres_database_keeps_legacy_environment_name(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.setenv("POSTGRES_DATABASE", "legacy-db")

    assert PostgresSettings().database == "legacy-db"

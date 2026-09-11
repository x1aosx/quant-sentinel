from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: SecretStr = SecretStr("")
    database: str = Field(
        default="xquant",
        validation_alias=AliasChoices("POSTGRES_DB", "POSTGRES_DATABASE", "database"),
    )

    @property
    def url(self) -> str:
        password = quote_plus(self.password.get_secret_value())
        return (
            f"postgresql+psycopg://{quote_plus(self.user)}:{password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @classmethod
    def from_config_values(cls, values: list[str]) -> PostgresSettings:
        return cls(
            host=values[0],
            port=int(values[1]),
            database=values[2],
            user=values[3],
            password=SecretStr(values[4]),
        )


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    host: str = "localhost"
    port: int = 6379
    password: SecretStr = SecretStr("")
    db: int = 0
    ssl: bool = False
    key_prefix: str = "xqs:v1"
    socket_timeout_seconds: float = 3.0

    @property
    def url(self) -> str:
        scheme = "rediss" if self.ssl else "redis"
        password = quote_plus(self.password.get_secret_value())
        auth = f":{password}@" if password else ""
        return f"{scheme}://{auth}{self.host}:{self.port}/{self.db}"

    @classmethod
    def from_config_values(cls, values: list[str]) -> RedisSettings:
        return cls(
            host=values[0],
            port=int(values[1]),
            password=SecretStr(values[2]) if values[2] else SecretStr(""),
        )


class InfluxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFLUXDB_", extra="ignore")

    url: str = "http://localhost:8181"
    token: SecretStr = SecretStr("")
    database: str = "xquant_market"
    timeout_seconds: float = 30.0
    write_path: str = "/api/v3/write_lp"
    query_path: str = "/api/v3/query_sql"

    @property
    def auth_header(self) -> str:
        return f"Bearer {self.token.get_secret_value()}"

    @classmethod
    def from_config_values(cls, values: list[str]) -> InfluxSettings:
        return cls(
            url=values[0],
            database=values[1],
            token=SecretStr(values[2]) if values[2] else SecretStr(""),
        )


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    storage_backend: Literal["postgres", "legacy_sqlite"] = Field(
        default="legacy_sqlite",
        validation_alias="STORAGE_BACKEND",
    )
    auto_migrate: bool = Field(
        default=True,
        validation_alias="STORAGE_AUTO_MIGRATE",
    )
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    influx: InfluxSettings = Field(default_factory=InfluxSettings)

    @classmethod
    def load(cls) -> StorageSettings:
        config_file = os.getenv("XQUANT_STORAGE_CONFIG_FILE")
        if config_file:
            return cls.from_config_file(config_file)
        return cls()

    @classmethod
    def from_config_file(cls, path: Path | str) -> StorageSettings:
        config_path = Path(path)
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for raw_line in config_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower() in {"postgresql", "redis", "s3", "influxdb"}:
                current = line.lower()
                sections.setdefault(current, [])
            elif current is not None:
                sections[current].append(line)

        return cls(
            storage_backend="postgres",
            postgres=PostgresSettings.from_config_values(sections["postgresql"]),
            redis=RedisSettings.from_config_values(sections["redis"]),
            influx=InfluxSettings.from_config_values(sections["influxdb"]),
        )

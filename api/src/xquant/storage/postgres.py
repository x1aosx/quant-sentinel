from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .settings import PostgresSettings


class PostgresStore:
    def __init__(
        self,
        settings: PostgresSettings,
        *,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout_seconds: int = 30,
        echo: bool = False,
        engine: Engine | None = None,
    ) -> None:
        self.settings = settings
        self._engine = engine or create_engine(
            settings.url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
        )

    def execute(
        self,
        statement: str,
        params: Mapping[str, Any] | None = None,
        *,
        fetch: str = "none",
    ) -> Any:
        with self._engine.begin() as connection:
            result = connection.execute(text(statement), dict(params or {}))
            if fetch == "all":
                return [dict(row._mapping) for row in result]
            if fetch == "one":
                row = result.first()
                return dict(row._mapping) if row else None
            return None

    def query(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        return self.execute(statement, params, fetch="all")

    def query_one(self, statement: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        return self.execute(statement, params, fetch="one")

    def execute_many(self, statement: str, params: Sequence[Mapping[str, Any]]) -> None:
        with self._engine.begin() as connection:
            connection.execute(text(statement), [dict(params) for params in params])

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._engine.begin() as connection:
            yield connection

    def health(self) -> dict[str, str]:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    def close(self) -> None:
        self._engine.dispose()

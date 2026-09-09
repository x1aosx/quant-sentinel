from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..storage import (
    InfluxDBStore,
    PostgresStore,
    RedisStore,
    StorageSettings,
)
from .sqlite import Database as LegacySqliteDatabase


class Database:
    """Persistence facade used by application and service methods."""

    def __init__(
        self,
        settings: StorageSettings,
        postgres: PostgresStore,
        redis_store: RedisStore | None,
        influx: InfluxDBStore,
    ) -> None:
        self.settings = settings
        self.postgres = postgres
        self.redis = redis_store
        self.influx = influx
        self._migrate()

    @classmethod
    def from_settings(
        cls,
        settings: StorageSettings,
        db_path: Path | None = None,
    ) -> Database | LegacySqliteDatabase:
        if settings.storage_backend == "legacy_sqlite":
            resolved_path = db_path or Path("data/xquant.db")
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            return LegacySqliteDatabase(resolved_path)

        return cls(
            settings=settings,
            postgres=PostgresStore(settings.postgres),
            redis_store=RedisStore(settings.redis),
            influx=InfluxDBStore(settings.influx),
        )

    def _migrate(self) -> None:
        if not self.settings.auto_migrate:
            return
        self.postgres.execute(
            """
            CREATE SCHEMA IF NOT EXISTS strategy;
            CREATE SCHEMA IF NOT EXISTS research;
            CREATE TABLE IF NOT EXISTS strategy.definition (
                id UUID PRIMARY KEY,
                version TEXT NOT NULL,
                title TEXT,
                research_status TEXT,
                evidence_level TEXT,
                market TEXT,
                frequency TEXT,
                source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS strategy.experiment (
                id UUID PRIMARY KEY,
                strategy_id UUID REFERENCES strategy.definition(id),
                status TEXT,
                run_id TEXT,
                summary JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS strategy.instance (
                id UUID PRIMARY KEY,
                strategy_id UUID REFERENCES strategy.definition(id),
                account_id TEXT,
                mode TEXT,
                status TEXT,
                created_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS strategy.plan (
                id UUID PRIMARY KEY,
                strategy_id UUID REFERENCES strategy.definition(id),
                instrument_id TEXT,
                status TEXT,
                simulation_only BOOLEAN NOT NULL DEFAULT TRUE,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS research.dataset (
                id UUID PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                bar_count INTEGER NOT NULL,
                first_session TEXT NOT NULL,
                last_session TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dataset_created_at
                ON research.dataset (created_at DESC);
            """
        )

    def upsert_strategy(self, strategy: dict[str, Any]) -> None:
        self.postgres.execute(
            """
            INSERT INTO strategy.definition
                (id, version, title, research_status, evidence_level, market, frequency,
                 source_refs, tags, created_at)
            VALUES
                (:id, :version, :title, :research_status, :evidence_level, :market,
                 :frequency, CAST(:source_refs AS jsonb), CAST(:tags AS jsonb),
                 CAST(:created_at AS timestamptz))
            ON CONFLICT (id) DO UPDATE SET
                version = excluded.version,
                title = excluded.title,
                research_status = excluded.research_status,
                evidence_level = excluded.evidence_level,
                market = excluded.market,
                frequency = excluded.frequency,
                source_refs = excluded.source_refs,
                tags = excluded.tags
            """,
            {
                **strategy,
                "source_refs": json.dumps(strategy.get("source_refs", []), ensure_ascii=False),
                "tags": json.dumps(strategy.get("tags", []), ensure_ascii=False),
            },
        )
        self._invalidate("strategy:list")

    def list_strategies(self) -> list[dict[str, Any]]:
        return self._cache_get_or_set(
            "strategy:list",
            lambda: self.postgres.query(
                """
                SELECT id::text, version, title, research_status, evidence_level, market,
                       frequency, source_refs, tags, created_at
                FROM strategy.definition
                ORDER BY id
                """
            ),
            ttl_seconds=30,
        )

    def insert_experiment(self, experiment: dict[str, Any]) -> None:
        self.postgres.execute(
            """
            INSERT INTO strategy.experiment
                (id, strategy_id, status, run_id, summary, created_at)
            VALUES
                (:id, :strategy_id, :status, :run_id, CAST(:summary AS jsonb),
                 CAST(:created_at AS timestamptz))
            ON CONFLICT (id) DO UPDATE SET
                strategy_id = excluded.strategy_id,
                status = excluded.status,
                run_id = excluded.run_id,
                summary = excluded.summary,
                created_at = excluded.created_at
            """,
            {
                **experiment,
                "summary": json.dumps(experiment.get("summary", {}), ensure_ascii=False),
            },
        )
        self._invalidate("experiment:list")

    def list_experiments(self) -> list[dict[str, Any]]:
        return self._cache_get_or_set(
            "experiment:list",
            lambda: self.postgres.query(
                """
                SELECT id::text, strategy_id::text, status, run_id, summary, created_at
                FROM strategy.experiment
                ORDER BY created_at DESC
                """
            ),
            ttl_seconds=30,
        )

    def insert_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        bars = payload.get("bars", [])
        dataset_id = str(payload.get("id") or uuid4())
        created_at = payload.get("created_at") or datetime.now(UTC)
        self.postgres.execute(
            """
            INSERT INTO research.dataset
                (id, symbol, timeframe, bar_count, first_session, last_session, created_at)
            VALUES
                (:id, :symbol, :timeframe, :bar_count, :first_session, :last_session,
                 CAST(:created_at AS timestamptz))
            ON CONFLICT (id) DO UPDATE SET
                symbol = excluded.symbol,
                timeframe = excluded.timeframe,
                bar_count = excluded.bar_count,
                first_session = excluded.first_session,
                last_session = excluded.last_session,
                created_at = excluded.created_at
            """,
            {
                "id": dataset_id,
                "symbol": payload["symbol"],
                "timeframe": payload["timeframe"],
                "bar_count": len(bars),
                "first_session": bars[0]["session_id"] if bars else "",
                "last_session": bars[-1]["session_id"] if bars else "",
                "created_at": created_at,
            },
        )
        self._write_dataset_bars(dataset_id, payload["symbol"], payload["timeframe"], bars, created_at)
        self._invalidate("dataset:list", f"dataset:{dataset_id}:bars")
        return self.get_dataset(dataset_id)["summary"]

    def list_datasets(self) -> list[dict[str, Any]]:
        return self._cache_get_or_set(
            "dataset:list",
            lambda: self.postgres.query(
                """
                SELECT id::text, symbol, timeframe, bar_count, first_session, last_session, created_at
                FROM research.dataset
                ORDER BY created_at DESC
                """
            ),
            ttl_seconds=30,
        )

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        metadata = self.postgres.query_one(
            """
            SELECT id::text, symbol, timeframe, bar_count, first_session, last_session, created_at
            FROM research.dataset
            WHERE id = CAST(:dataset_id AS uuid)
            """,
            {"dataset_id": dataset_id},
        )
        if metadata is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        bars = self._cache_get_or_set(
            f"dataset:{metadata['id']}:bars",
            lambda: self._fetch_dataset_bars(metadata["id"]),
            ttl_seconds=120,
        )
        return {"summary": metadata, "bars": bars}

    def storage_health(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        checks = {
            "postgres": self.postgres.health,
            "redis": self.redis.health if self.redis else None,
            "influxdb": self.influx.health,
        }
        for name, check in checks.items():
            if check is None:
                result[name] = {"status": "disabled"}
                continue
            started = time.perf_counter()
            try:
                status = check()
            except (RedisError, SQLAlchemyError, OSError) as exc:
                result[name] = {"status": "error", "error": type(exc).__name__}
            else:
                result[name] = {
                    "status": status.get("status", "ok"),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
        return result

    def close(self) -> None:
        if self.redis:
            self.redis.close()
        self.postgres.close()
        self.influx.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _cache_get_or_set(self, key: str, factory: Any, *, ttl_seconds: int) -> Any:
        if self.redis is None:
            return factory()
        try:
            cached = self.redis.get_json(key)
            if cached is not None:
                return cached
            value = factory()
            self.redis.set_json(key, value, ttl_seconds)
            return value
        except (RedisError, OSError):
            return factory()

    def _invalidate(self, *keys: str) -> None:
        if self.redis is not None:
            try:
                self.redis.delete(*keys)
            except (RedisError, OSError):
                return

    def _write_dataset_bars(
        self,
        dataset_id: str,
        symbol: str,
        timeframe: str,
        bars: list[dict[str, Any]],
        created_at: Any,
    ) -> None:
        points: list[dict[str, Any]] = []
        fallback_time = _parse_time(created_at) or datetime.now(UTC)
        for index, bar in enumerate(bars):
            bar_time = _parse_time(bar.get("session_id")) or fallback_time
            bar_time = bar_time + timedelta(microseconds=index % 1000)
            points.append(
                {
                    "measurement": "market_bar",
                    "tags": {
                        "dataset_id": dataset_id,
                        "symbol": symbol,
                        "timeframe": timeframe,
                    },
                    "fields": {
                        "session_id": bar["session_id"],
                        "source_seq": index,
                        "open": float(bar["open"]),
                        "high": float(bar["high"]),
                        "low": float(bar["low"]),
                        "close": float(bar["close"]),
                        "volume": float(bar["volume"]),
                    },
                    "time": bar_time,
                }
            )
        if points:
            self.influx.write_points(points)

    def _fetch_dataset_bars(self, dataset_id: str) -> list[dict[str, Any]]:
        rows = self.influx.query(
            """
            SELECT session_id, source_seq, open, high, low, close, volume
            FROM market_bar
            WHERE dataset_id = $dataset_id
            ORDER BY time ASC, source_seq ASC
            """,
            {"dataset_id": dataset_id},
        )
        bars: list[dict[str, Any]] = []
        for row in rows:
            bars.append(
                {
                    "session_id": str(row["session_id"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
        return bars


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

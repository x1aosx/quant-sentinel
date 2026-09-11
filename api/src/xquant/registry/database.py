from __future__ import annotations

import json
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..marketdata.remote import fetch_remote_bars, resolve_instrument_title
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
                title TEXT,
                timeframe TEXT NOT NULL,
                bar_count INTEGER NOT NULL,
                first_session TEXT NOT NULL,
                last_session TEXT NOT NULL,
                source TEXT,
                source_provider TEXT,
                exchange TEXT,
                last_synced_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL
            );
            ALTER TABLE research.dataset
                ADD COLUMN IF NOT EXISTS source TEXT;
            ALTER TABLE research.dataset
                ADD COLUMN IF NOT EXISTS title TEXT;
            ALTER TABLE research.dataset
                ADD COLUMN IF NOT EXISTS source_provider TEXT;
            ALTER TABLE research.dataset
                ADD COLUMN IF NOT EXISTS exchange TEXT;
            ALTER TABLE research.dataset
                ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
            CREATE INDEX IF NOT EXISTS idx_dataset_created_at
                ON research.dataset (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_dataset_symbol_timeframe
                ON research.dataset (symbol, timeframe);
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
        source = str(payload.get("source") or "local")
        source_provider = str(payload.get("source_provider") or "local_file")
        exchange = str(payload.get("exchange") or "")
        title = str(payload.get("title") or payload["symbol"]).strip()
        last_synced_at = payload.get("last_synced_at") or created_at
        self.postgres.execute(
            """
            INSERT INTO research.dataset
                (id, symbol, title, timeframe, bar_count, first_session, last_session,
                 source, source_provider, exchange, last_synced_at, created_at)
            VALUES
                (:id, :symbol, :title, :timeframe, :bar_count, :first_session, :last_session,
                 :source, :source_provider, :exchange, CAST(:last_synced_at AS timestamptz),
                 CAST(:created_at AS timestamptz))
            ON CONFLICT (id) DO UPDATE SET
                symbol = excluded.symbol,
                title = excluded.title,
                timeframe = excluded.timeframe,
                bar_count = excluded.bar_count,
                first_session = excluded.first_session,
                last_session = excluded.last_session,
                source = excluded.source,
                source_provider = excluded.source_provider,
                exchange = excluded.exchange,
                last_synced_at = excluded.last_synced_at,
                created_at = excluded.created_at
            """,
            {
                "id": dataset_id,
                "symbol": payload["symbol"],
                "title": title,
                "timeframe": payload["timeframe"],
                "bar_count": len(bars),
                "first_session": bars[0]["session_id"] if bars else "",
                "last_session": bars[-1]["session_id"] if bars else "",
                "source": source,
                "source_provider": source_provider,
                "exchange": exchange,
                "last_synced_at": last_synced_at,
                "created_at": created_at,
            },
        )
        self._write_dataset_bars(dataset_id, payload["symbol"], payload["timeframe"], bars, created_at)
        self._invalidate("dataset:list", f"dataset:{dataset_id}:bars")
        return self.get_dataset(dataset_id)["summary"]

    def sync_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = dict(payload)
        requested_symbol = str(request_payload.get("symbol") or "").strip().upper()
        requested_timeframe = str(request_payload.get("timeframe") or "1d").strip().lower() or "1d"
        existing = (
            self._find_dataset(requested_symbol, requested_timeframe) if requested_symbol else None
        )
        if existing and not request_payload.get("session_start"):
            request_payload["session_start"] = existing["last_session"]

        remote = fetch_remote_bars(request_payload)
        symbol = str(remote.get("symbol") or requested_symbol).strip().upper()
        timeframe = str(remote.get("timeframe") or requested_timeframe).strip() or "1d"
        if not symbol:
            raise ValueError("远程行情返回的 symbol 不能为空")
        if existing is None or symbol != requested_symbol or timeframe != requested_timeframe:
            existing = self._find_dataset(symbol, timeframe)

        raw_bars = remote.get("bars")
        if raw_bars is None:
            raise ValueError("远程行情返回的 bars 不能为空")
        if not isinstance(raw_bars, list):
            raise TypeError("远程行情返回的 bars 格式无效")
        remote_bars = _dedupe_bars(raw_bars)

        dataset_id = str(existing["id"]) if existing else _dataset_id(symbol, timeframe)
        created_at = _parse_time(existing.get("created_at")) if existing else None
        now = datetime.now(UTC)
        created_at = created_at or now
        existing_bars = self._fetch_dataset_bars(dataset_id) if existing else []
        existing_by_session = {
            bar["session_id"]: bar for bar in _dedupe_bars(existing_bars).values()
        }

        new_bars = [
            bar for session_id, bar in remote_bars.items() if session_id not in existing_by_session
        ]
        updated_count = len(remote_bars) - len(new_bars)
        merged = {**existing_by_session, **{bar["session_id"]: bar for bar in new_bars}}
        merged_bars = [merged[session_id] for session_id in sorted(merged)]
        source = str(remote.get("source") or request_payload.get("source") or "unknown")
        source_provider = str(remote.get("source_provider") or source)
        exchange = str(remote.get("exchange") or request_payload.get("exchange") or "")
        title = (
            str(remote.get("title") or "").strip()
            or resolve_instrument_title(symbol, exchange=exchange, source=source)
            or str(existing.get("title") if existing else "").strip()
            or symbol
        )

        if new_bars:
            self._write_dataset_bars(
                dataset_id,
                symbol,
                timeframe,
                new_bars,
                created_at,
            )
        self._upsert_dataset_metadata(
            dataset_id=dataset_id,
            symbol=symbol,
            title=title,
            timeframe=timeframe,
            bars=merged_bars,
            source=source,
            source_provider=source_provider,
            exchange=exchange,
            last_synced_at=now,
            created_at=created_at,
        )
        self._invalidate("dataset:list", f"dataset:{dataset_id}:bars")
        summary = self.get_dataset(dataset_id)["summary"]
        return {
            "dataset": summary,
            "id": dataset_id,
            "symbol": symbol,
            "title": title,
            "timeframe": timeframe,
            "bar_count": len(merged_bars),
            "first_session": merged_bars[0]["session_id"] if merged_bars else "",
            "last_session": merged_bars[-1]["session_id"] if merged_bars else "",
            "created_at": summary["created_at"],
            "source": source,
            "source_provider": source_provider,
            "exchange": exchange or None,
            "inserted_count": len(new_bars),
            "updated_count": updated_count,
            "total_count": len(merged_bars),
            "sync_status": "updated" if new_bars else "unchanged",
            "synced_at": now.isoformat(),
        }

    def list_datasets(self) -> list[dict[str, Any]]:
        datasets = self._cache_get_or_set(
            "dataset:list",
            lambda: self.postgres.query(
                """
                SELECT id::text, symbol, timeframe, bar_count, first_session, last_session,
                       NULLIF(title, '') AS stored_title,
                       COALESCE(NULLIF(title, ''), symbol) AS title,
                       COALESCE(source, 'local') AS source,
                       COALESCE(source_provider, 'local_file') AS source_provider,
                       exchange,
                       COALESCE(last_synced_at, created_at) AS last_synced_at,
                       created_at
                FROM research.dataset
                ORDER BY created_at DESC
                """
            ),
            ttl_seconds=30,
        )
        self._resolve_missing_dataset_titles(datasets)
        return datasets

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        metadata = self.postgres.query_one(
            """
            SELECT id::text, symbol, timeframe, bar_count, first_session, last_session,
                   NULLIF(title, '') AS stored_title,
                   COALESCE(NULLIF(title, ''), symbol) AS title,
                   COALESCE(source, 'local') AS source,
                   COALESCE(source_provider, 'local_file') AS source_provider,
                   exchange,
                   COALESCE(last_synced_at, created_at) AS last_synced_at,
                   created_at
            FROM research.dataset
            WHERE id = CAST(:dataset_id AS uuid)
            """,
            {"dataset_id": dataset_id},
        )
        if metadata is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        self._resolve_missing_dataset_titles([metadata])
        bars = self._cache_get_or_set(
            f"dataset:{metadata['id']}:bars",
            lambda: self._fetch_dataset_bars(metadata["id"]),
            ttl_seconds=120,
        )
        return {"summary": metadata, "bars": bars}

    def delete_dataset(self, dataset_id: str) -> dict[str, Any]:
        try:
            UUID(dataset_id)
        except ValueError as exc:
            raise KeyError(f"dataset not found: {dataset_id}") from exc
        metadata = self.postgres.query_one(
            """
            SELECT id::text
            FROM research.dataset
            WHERE id = CAST(:dataset_id AS uuid)
            """,
            {"dataset_id": dataset_id},
        )
        if metadata is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        self.influx.delete_market_bars(dataset_id)
        self.postgres.execute(
            """
            DELETE FROM research.dataset
            WHERE id = CAST(:dataset_id AS uuid)
            """,
            {"dataset_id": dataset_id},
        )
        self._invalidate("dataset:list", f"dataset:{dataset_id}:bars")
        return {"deleted": True, "id": dataset_id}

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
            session_id = str(bar["session_id"])
            bar_time = _parse_time(session_id)
            if bar_time is None:
                offset = zlib.crc32(session_id.encode("utf-8")) % 1_000_000
                bar_time = fallback_time.replace(microsecond=offset)
            points.append(
                {
                    "measurement": "market_bar",
                    "tags": {
                        "dataset_id": dataset_id,
                        "symbol": symbol,
                        "timeframe": timeframe,
                    },
                    "fields": {
                        "session_id": session_id,
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

    def _find_dataset(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        return self.postgres.query_one(
            """
            SELECT id::text, symbol, timeframe, bar_count, first_session, last_session,
                   COALESCE(NULLIF(title, ''), symbol) AS title,
                   COALESCE(source, 'local') AS source,
                   COALESCE(source_provider, 'local_file') AS source_provider,
                   exchange,
                   COALESCE(last_synced_at, created_at) AS last_synced_at,
                   created_at
            FROM research.dataset
            WHERE UPPER(symbol) = UPPER(:symbol) AND timeframe = :timeframe
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"symbol": symbol, "timeframe": timeframe},
        )

    def _upsert_dataset_metadata(
        self,
        *,
        dataset_id: str,
        symbol: str,
        title: str,
        timeframe: str,
        bars: list[dict[str, Any]],
        source: str,
        source_provider: str,
        exchange: str,
        last_synced_at: datetime,
        created_at: datetime,
    ) -> None:
        self.postgres.execute(
            """
            INSERT INTO research.dataset
                (id, symbol, title, timeframe, bar_count, first_session, last_session,
                 source, source_provider, exchange, last_synced_at, created_at)
            VALUES
                (:id, :symbol, :title, :timeframe, :bar_count, :first_session, :last_session,
                 :source, :source_provider, :exchange, CAST(:last_synced_at AS timestamptz),
                 CAST(:created_at AS timestamptz))
            ON CONFLICT (id) DO UPDATE SET
                symbol = excluded.symbol,
                title = excluded.title,
                timeframe = excluded.timeframe,
                bar_count = excluded.bar_count,
                first_session = excluded.first_session,
                last_session = excluded.last_session,
                source = excluded.source,
                source_provider = excluded.source_provider,
                exchange = excluded.exchange,
                last_synced_at = excluded.last_synced_at
            """,
            {
                "id": dataset_id,
                "symbol": symbol,
                "title": title,
                "timeframe": timeframe,
                "bar_count": len(bars),
                "first_session": bars[0]["session_id"] if bars else "",
                "last_session": bars[-1]["session_id"] if bars else "",
                "source": source,
                "source_provider": source_provider,
                "exchange": exchange,
                "last_synced_at": last_synced_at,
                "created_at": created_at,
            },
        )

    def _resolve_missing_dataset_titles(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            stored_title = str(row.pop("stored_title", "") or "").strip()
            if stored_title and stored_title != symbol:
                continue
            if not symbol:
                row["title"] = stored_title
                continue
            title = resolve_instrument_title(
                symbol,
                exchange=str(row.get("exchange") or ""),
                source=str(row.get("source") or ""),
            )
            if not title:
                row["title"] = stored_title or symbol
                continue
            row["title"] = title
            if title == stored_title:
                continue
            try:
                self.postgres.execute(
                    """
                    UPDATE research.dataset
                    SET title = :title
                    WHERE id = CAST(:dataset_id AS uuid)
                    """,
                    {"dataset_id": row["id"], "title": title},
                )
            except SQLAlchemyError:
                continue

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


def _dedupe_bars(bars: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for index, bar in enumerate(bars):
        try:
            session_id = str(bar["session_id"]).strip()
            normalized = {
                "session_id": session_id,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume") or 0.0),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index + 1} 条 bar 数据无效") from exc
        if not session_id:
            raise ValueError(f"第 {index + 1} 条 bar 缺少 session_id")
        deduped.setdefault(session_id, normalized)
    return deduped


def _dataset_id(symbol: str, timeframe: str) -> str:
    identity = f"https://xquant.local/datasets/{symbol.strip().upper()}/{timeframe.strip().lower()}"
    return str(uuid5(NAMESPACE_URL, identity))

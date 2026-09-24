from __future__ import annotations

import json
import time
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
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
from .bar_utils import dedupe_sorted_bars
from .bar_utils import parse_session_time as _parse_time
from .bar_utils import session_identity, session_sort_key as _session_sort_key
from .sqlite import Database as LegacySqliteDatabase
from .sqlite import (
    _normalize_record_limit,
    _normalize_record_offset,
    _record_created_at,
    _record_dataset_id,
    _record_summary,
    _sanitize_json,
)

_MONITOR_LOG_RETENTION_DAYS = 30
_MONITOR_LOG_PRUNE_INTERVAL_SECONDS = 600.0


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
        # 盯盘日志按时间保留，避免 60s 轮询下无限增长；剪枝带节流。
        self._monitor_log_pruned_at = 0.0
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
                deleted_at TIMESTAMPTZ,
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
            ALTER TABLE research.dataset
                ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
            CREATE INDEX IF NOT EXISTS idx_dataset_created_at
                ON research.dataset (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_dataset_symbol_timeframe
                ON research.dataset (symbol, timeframe);
            CREATE INDEX IF NOT EXISTS idx_dataset_active
                ON research.dataset (deleted_at)
                WHERE deleted_at IS NULL;
            CREATE TABLE IF NOT EXISTS research.ai_analysis_record (
                id UUID PRIMARY KEY,
                record_id TEXT NOT NULL,
                dataset_id TEXT,
                symbol TEXT,
                timeframe TEXT,
                status TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                duration_ms DOUBLE PRECISION,
                decision_action TEXT,
                confidence DOUBLE PRECISION,
                record JSONB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_analysis_record_created_at
                ON research.ai_analysis_record (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_analysis_record_dataset_created_at
                ON research.ai_analysis_record (dataset_id, created_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS research.monitor_run_log (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL,
                target_key TEXT,
                dataset_id TEXT,
                symbol TEXT,
                timeframe TEXT,
                status TEXT,
                session_id TEXT,
                message TEXT,
                detail JSONB NOT NULL DEFAULT '{}'::jsonb
            );
            CREATE INDEX IF NOT EXISTS idx_monitor_run_log_created_at
                ON research.monitor_run_log (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_monitor_run_log_dataset_created_at
                ON research.monitor_run_log (dataset_id, created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_monitor_run_log_symbol_created_at
                ON research.monitor_run_log (symbol, created_at DESC, id DESC);
            """
        )
        self.postgres.execute(
            """
            DELETE FROM research.ai_analysis_record older
            USING research.ai_analysis_record newer
            WHERE older.symbol IS NOT NULL
              AND older.timeframe IS NOT NULL
              AND older.symbol = newer.symbol
              AND older.timeframe = newer.timeframe
              AND (
                  older.created_at < newer.created_at
                  OR (older.created_at = newer.created_at AND older.id < newer.id)
              )
            """
        )
        self.postgres.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_analysis_record_symbol_timeframe
                ON research.ai_analysis_record (symbol, timeframe)
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
                deleted_at = NULL,
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
        existing_bars = self._fetch_dataset_bars(dataset_id, existing) if existing else []
        existing_by_session = _dedupe_bars(existing_bars)

        new_bars = [
            bar for identity, bar in remote_bars.items() if identity not in existing_by_session
        ]
        revised_bars = [
            bar
            for identity, bar in remote_bars.items()
            if identity in existing_by_session
            and existing_by_session[identity] != bar
        ]
        bars_to_write = [*new_bars, *revised_bars]
        updated_count = len(remote_bars) - len(new_bars)
        merged = {**existing_by_session, **remote_bars}
        merged_bars = sorted(
            merged.values(),
            key=lambda bar: _session_sort_key(str(bar.get("session_id") or "")),
        )
        source = str(remote.get("source") or request_payload.get("source") or "unknown")
        source_provider = str(remote.get("source_provider") or source)
        exchange = str(remote.get("exchange") or request_payload.get("exchange") or "")
        title = (
            str(remote.get("title") or "").strip()
            or resolve_instrument_title(symbol, exchange=exchange, source=source)
            or str(existing.get("title") if existing else "").strip()
            or symbol
        )

        if bars_to_write:
            self._write_dataset_bars(
                dataset_id,
                symbol,
                timeframe,
                bars_to_write,
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
            "sync_status": "updated" if new_bars or revised_bars else "unchanged",
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
                WHERE deleted_at IS NULL
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
              AND deleted_at IS NULL
            """,
            {"dataset_id": dataset_id},
        )
        if metadata is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        self._resolve_missing_dataset_titles([metadata])
        bars = self._cache_get_or_set(
            f"dataset:{metadata['id']}:bars",
            lambda: self._fetch_dataset_bars(metadata["id"], metadata),
            ttl_seconds=120,
        )
        bars = dedupe_sorted_bars(bars)
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
              AND deleted_at IS NULL
            """,
            {"dataset_id": dataset_id},
        )
        if metadata is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        self.postgres.execute(
            """
            UPDATE research.dataset
            SET deleted_at = now()
            WHERE id = CAST(:dataset_id AS uuid)
              AND deleted_at IS NULL
            """,
            {"dataset_id": dataset_id},
        )
        self._invalidate("dataset:list", f"dataset:{dataset_id}:bars")
        return {"deleted": True, "id": dataset_id}

    def save_analysis_record(
        self,
        record: dict[str, Any],
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        persisted_id = str(uuid4())
        stored_dataset_id = _record_dataset_id(record, dataset_id)
        created_at = _record_created_at(record)
        summary = _record_summary(
            record,
            persisted_id=persisted_id,
            dataset_id=stored_dataset_id,
            created_at=created_at,
        )
        stored_record = _sanitize_json(record)
        self.postgres.execute(
            """
            INSERT INTO research.ai_analysis_record AS current_record
                (id, record_id, dataset_id, symbol, timeframe, status, created_at,
                 duration_ms, decision_action, confidence, record)
            VALUES
                (CAST(:id AS uuid), :record_id, :dataset_id, :symbol, :timeframe,
                 :status, CAST(:created_at AS timestamptz), :duration_ms,
                 :decision_action, :confidence, CAST(:record AS jsonb))
            ON CONFLICT (symbol, timeframe) DO UPDATE SET
                id = EXCLUDED.id,
                record_id = EXCLUDED.record_id,
                dataset_id = EXCLUDED.dataset_id,
                symbol = EXCLUDED.symbol,
                timeframe = EXCLUDED.timeframe,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at,
                duration_ms = EXCLUDED.duration_ms,
                decision_action = EXCLUDED.decision_action,
                confidence = EXCLUDED.confidence,
                record = EXCLUDED.record
            WHERE EXCLUDED.created_at >= current_record.created_at
            """,
            {
                **summary,
                "record": json.dumps(
                    stored_record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        )
        return summary

    def list_analysis_records(
        self,
        dataset_id: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        normalized_limit = _normalize_record_limit(limit)
        normalized_offset = _normalize_record_offset(offset)
        columns = """
            SELECT r.id::text, r.record_id, r.dataset_id, r.symbol, r.timeframe, r.status,
                   r.created_at, r.duration_ms, r.decision_action, r.confidence,
                   d.title AS title
            FROM research.ai_analysis_record r
            LEFT JOIN research.dataset d ON d.id::text = r.dataset_id
        """
        if dataset_id is not None:
            rows = self.postgres.query(
                columns
                + """
                WHERE r.dataset_id = :dataset_id
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT :limit OFFSET :offset
                """,
                {
                    "dataset_id": dataset_id,
                    "limit": normalized_limit,
                    "offset": normalized_offset,
                },
            )
        elif symbol is not None or timeframe is not None:
            clauses: list[str] = []
            params: dict[str, Any] = {
                "limit": normalized_limit,
                "offset": normalized_offset,
            }
            if symbol is not None:
                clauses.append("r.symbol = :symbol")
                params["symbol"] = symbol
            if timeframe is not None:
                clauses.append("r.timeframe = :timeframe")
                params["timeframe"] = timeframe
            rows = self.postgres.query(
                columns
                + f"""
                WHERE {' AND '.join(clauses)}
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT :limit OFFSET :offset
                """,
                params,
            )
        else:
            rows = self.postgres.query(
                columns
                + """
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT :limit OFFSET :offset
                """,
                {"limit": normalized_limit, "offset": normalized_offset},
            )
        summary_fields = (
            "id",
            "record_id",
            "dataset_id",
            "symbol",
            "timeframe",
            "status",
            "created_at",
            "duration_ms",
            "decision_action",
            "confidence",
            "title",
        )
        return [{field: row.get(field) for field in summary_fields} for row in rows]

    def count_analysis_records(
        self,
        dataset_id: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> int:
        query = """
            SELECT COUNT(*) AS total
            FROM research.ai_analysis_record
        """
        if dataset_id is not None:
            row = self.postgres.query_one(
                query
                + """
                WHERE dataset_id = :dataset_id
                """,
                {"dataset_id": dataset_id},
            )
        elif symbol is not None or timeframe is not None:
            clauses: list[str] = []
            params: dict[str, Any] = {}
            if symbol is not None:
                clauses.append("symbol = :symbol")
                params["symbol"] = symbol
            if timeframe is not None:
                clauses.append("timeframe = :timeframe")
                params["timeframe"] = timeframe
            row = self.postgres.query_one(
                query
                + f"""
                WHERE {' AND '.join(clauses)}
                """,
                params,
            )
        else:
            row = self.postgres.query_one(query)
        return int(row.get("total", 0)) if row is not None else 0

    def get_analysis_record(self, record_id: str) -> dict[str, Any]:
        try:
            UUID(record_id)
        except ValueError as exc:
            raise KeyError(f"analysis record not found: {record_id}") from exc
        row = self.postgres.query_one(
            """
            SELECT id::text, record_id, dataset_id, created_at, record
            FROM research.ai_analysis_record
            WHERE id = CAST(:record_id AS uuid)
            """,
            {"record_id": record_id},
        )
        if row is None:
            raise KeyError(f"analysis record not found: {record_id}")
        payload = row.get("record")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {
            "id": row["id"],
            "record_id": row.get("record_id"),
            "dataset_id": row.get("dataset_id"),
            "created_at": row.get("created_at"),
            "record": payload,
        }

    def delete_analysis_record(self, record_id: str) -> dict[str, Any]:
        try:
            UUID(record_id)
        except ValueError as exc:
            raise KeyError(f"analysis record not found: {record_id}") from exc
        row = self.postgres.query_one(
            """
            SELECT id::text
            FROM research.ai_analysis_record
            WHERE id = CAST(:record_id AS uuid)
            """,
            {"record_id": record_id},
        )
        if row is None:
            raise KeyError(f"analysis record not found: {record_id}")
        self.postgres.execute(
            """
            DELETE FROM research.ai_analysis_record
            WHERE id = CAST(:record_id AS uuid)
            """,
            {"record_id": record_id},
        )
        return {"deleted": True, "id": record_id}

    def save_monitor_log(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        log_id = str(uuid4())
        created_at = _parse_time(entry.get("created_at")) or datetime.now(UTC)
        dataset_id = str(entry.get("dataset_id") or "").strip() or None
        symbol = str(entry.get("symbol") or "").strip().upper() or None
        timeframe = str(entry.get("timeframe") or "").strip().lower() or None
        status = str(entry.get("status") or "").strip() or None
        session_id = str(entry.get("session_id") or "").strip() or None
        message = str(entry.get("message") or "").strip() or None
        target_key = str(entry.get("target_key") or "").strip() or None
        detail = _sanitize_json(
            entry.get("detail") if isinstance(entry.get("detail"), Mapping) else {}
        )
        self.postgres.execute(
            """
            INSERT INTO research.monitor_run_log
                (id, created_at, target_key, dataset_id, symbol, timeframe, status,
                 session_id, message, detail)
            VALUES
                (CAST(:id AS uuid), CAST(:created_at AS timestamptz), :target_key,
                 :dataset_id, :symbol, :timeframe, :status, :session_id, :message,
                 CAST(:detail AS jsonb))
            """,
            {
                "id": log_id,
                "created_at": created_at.isoformat(),
                "target_key": target_key,
                "dataset_id": dataset_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "status": status,
                "session_id": session_id,
                "message": message,
                "detail": json.dumps(
                    detail,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        )
        self._maybe_prune_monitor_logs()
        return {
            "id": log_id,
            "created_at": created_at.isoformat(),
            "target_key": target_key,
            "dataset_id": dataset_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": status,
            "session_id": session_id,
            "message": message,
            "detail": detail,
        }

    def _maybe_prune_monitor_logs(self) -> None:
        now = time.monotonic()
        if now - self._monitor_log_pruned_at < _MONITOR_LOG_PRUNE_INTERVAL_SECONDS:
            return
        self._monitor_log_pruned_at = now
        cutoff = datetime.now(UTC) - timedelta(days=_MONITOR_LOG_RETENTION_DAYS)
        try:
            self.postgres.execute(
                """
                DELETE FROM research.monitor_run_log
                WHERE created_at < CAST(:cutoff AS timestamptz)
                """,
                {"cutoff": cutoff.isoformat()},
            )
        except Exception:  # noqa: BLE001 - retention is best-effort
            return

    def list_monitor_logs(
        self,
        dataset_id: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        normalized_limit = _normalize_record_limit(limit)
        normalized_offset = _normalize_record_offset(offset)
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": normalized_limit,
            "offset": normalized_offset,
        }
        if dataset_id:
            clauses.append("dataset_id = :dataset_id")
            params["dataset_id"] = dataset_id
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.postgres.query(
            f"""
            SELECT id::text, created_at, target_key, dataset_id, symbol, timeframe,
                   status, session_id, message, detail
            FROM research.monitor_run_log
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return [_monitor_log_row(row) for row in rows]

    def count_monitor_logs(
        self,
        dataset_id: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if dataset_id:
            clauses.append("dataset_id = :dataset_id")
            params["dataset_id"] = dataset_id
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        if status:
            clauses.append("status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.postgres.query_one(
            f"SELECT COUNT(*) AS total FROM research.monitor_run_log {where}",
            params,
        )
        return int(row.get("total", 0)) if row is not None else 0

    def delete_monitor_log(self, log_id: str) -> dict[str, Any]:
        try:
            UUID(log_id)
        except ValueError as exc:
            raise KeyError(f"monitor log not found: {log_id}") from exc
        row = self.postgres.query_one(
            "SELECT id::text FROM research.monitor_run_log WHERE id = CAST(:log_id AS uuid)",
            {"log_id": log_id},
        )
        if row is None:
            raise KeyError(f"monitor log not found: {log_id}")
        self.postgres.execute(
            "DELETE FROM research.monitor_run_log WHERE id = CAST(:log_id AS uuid)",
            {"log_id": log_id},
        )
        return {"deleted": True, "id": log_id}

    def clear_monitor_logs(
        self,
        dataset_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if dataset_id:
            clauses.append("dataset_id = :dataset_id")
            params["dataset_id"] = dataset_id
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        self.postgres.execute(f"DELETE FROM research.monitor_run_log {where}", params)
        return {"cleared": True}

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

    def get_cached_json(self, key: str) -> Any | None:
        if self.redis is None:
            return None
        try:
            return self.redis.get_json(key)
        except (RedisError, OSError, TypeError, ValueError):
            return None

    def set_cached_json(
        self,
        key: str,
        value: Any,
        ttl_seconds: int = 300,
    ) -> None:
        if self.redis is None:
            return
        try:
            self.redis.set_json(key, value, ttl_seconds)
        except (RedisError, OSError, TypeError, ValueError):
            return

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _cache_get_or_set(self, key: str, factory: Any, *, ttl_seconds: int) -> Any:
        cached = self.get_cached_json(key)
        if cached is not None:
            return cached
        value = factory()
        self.set_cached_json(key, value, ttl_seconds)
        return value

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
                last_synced_at = excluded.last_synced_at,
                deleted_at = NULL
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

    def _fetch_dataset_bars(
        self,
        dataset_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        bounds = _dataset_time_bounds(metadata)
        if bounds is None:
            rows = self.influx.query(
                _UNBOUNDED_BAR_QUERY_SQL,
                {"dataset_id": dataset_id},
            )
        else:
            rows = self.influx.query_time_windows(
                _BAR_TIME_WINDOW_QUERY_SQL,
                {"dataset_id": dataset_id},
                lower=bounds[0],
                upper=bounds[1],
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
                    "_source_seq": int(row.get("source_seq") or 0),
                }
            )
        bars.sort(
            key=lambda bar: (
                _session_sort_key(str(bar["session_id"])),
                int(bar["_source_seq"]),
            )
        )
        for bar in bars:
            bar.pop("_source_seq", None)
        return dedupe_sorted_bars(bars)


# 行情 bar 的查询必须带时间谓词：InfluxDB 3 才能按时间裁剪 parquet 文件。
_BAR_TIME_WINDOW_QUERY_SQL = """
SELECT session_id, source_seq, open, high, low, close, volume
FROM market_bar
WHERE dataset_id = $dataset_id
  AND time >= $time_lower
  AND time <= $time_upper
ORDER BY time ASC, source_seq ASC
"""

# 元数据推不出时间范围时的退化查询，行为与旧版一致。
_UNBOUNDED_BAR_QUERY_SQL = """
SELECT session_id, source_seq, open, high, low, close, volume
FROM market_bar
WHERE dataset_id = $dataset_id
ORDER BY time ASC, source_seq ASC
"""

# 时间范围两侧的余量，容忍 session_id 与 created_at 兜底时间之间的偏差。
_BAR_TIME_SLACK = timedelta(days=1)


def _dataset_time_bounds(
    metadata: Mapping[str, Any] | None,
) -> tuple[datetime, datetime] | None:
    """从数据集元数据推导 bar 的时间范围，供 InfluxDB 裁剪 parquet 文件。

    带时间谓词的查询才能让 InfluxDB 3 按文件的时间范围做裁剪，否则整张
    ``market_bar`` 表都要扫，很容易撞上 ``--query-file-limit``。

    session_id 能解析时取首末 session 时间；解析不了的 bar 在写入时会落到
    ``created_at`` 附近，因此把 created_at / last_synced_at 一并纳入候选值，
    保证推导出的范围不会漏掉任何已写入的 bar。
    """
    if not metadata:
        return None
    candidates = [
        parsed
        for key in ("first_session", "last_session", "created_at", "last_synced_at")
        if (parsed := _parse_time(metadata.get(key))) is not None
    ]
    if not candidates:
        return None
    return min(candidates) - _BAR_TIME_SLACK, max(candidates) + _BAR_TIME_SLACK


def _dedupe_bars(bars: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalise bars and key them by instant so timezone spellings collapse."""

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
        deduped.setdefault(session_identity(session_id), normalized)
    return deduped


def _monitor_log_row(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = row.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = {}
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {
        "id": str(row.get("id") or ""),
        "created_at": created_at,
        "target_key": row.get("target_key"),
        "dataset_id": row.get("dataset_id"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "status": row.get("status"),
        "session_id": row.get("session_id"),
        "message": row.get("message"),
        "detail": detail if isinstance(detail, Mapping) else {},
    }


def _dataset_id(symbol: str, timeframe: str) -> str:
    identity = f"https://xquant.local/datasets/{symbol.strip().upper()}/{timeframe.strip().lower()}"
    return str(uuid5(NAMESPACE_URL, identity))

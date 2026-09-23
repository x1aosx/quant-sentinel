from __future__ import annotations

import copy
import json
import math
import sqlite3
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from xquant.marketdata.remote import fetch_remote_bars, resolve_instrument_title

from .bar_utils import dedupe_sorted_bars, session_sort_key

_PROCESS_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_PROCESS_CACHE_LOCK = threading.Lock()


def _title_needs_resolution(title: Any, symbol: Any) -> bool:
    normalized_title = str(title or "").strip()
    normalized_symbol = str(symbol or "").strip()
    return not normalized_title or normalized_title == normalized_symbol


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
        conn = self._connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_definitions (
                id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                title TEXT,
                research_status TEXT,
                evidence_level TEXT,
                market TEXT,
                frequency TEXT,
                source_refs TEXT,
                tags TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                strategy_id TEXT,
                status TEXT,
                run_id TEXT,
                summary TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS instances (
                id TEXT PRIMARY KEY,
                strategy_id TEXT,
                account_id TEXT,
                mode TEXT,
                status TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                strategy_id TEXT,
                instrument_id TEXT,
                status TEXT,
                simulation_only INTEGER,
                payload TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                title TEXT,
                timeframe TEXT NOT NULL,
                bar_count INTEGER NOT NULL,
                first_session TEXT NOT NULL,
                last_session TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT,
                source_provider TEXT,
                exchange TEXT,
                last_synced_at TEXT,
                bars_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_analysis_records (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL,
                dataset_id TEXT,
                symbol TEXT,
                timeframe TEXT,
                status TEXT,
                created_at TEXT NOT NULL,
                duration_ms REAL,
                decision_action TEXT,
                confidence REAL,
                record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_analysis_records_created_at
                ON ai_analysis_records (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_analysis_records_dataset_created_at
                ON ai_analysis_records (dataset_id, created_at DESC);
            """
        )
        conn.execute(
            """
            DELETE FROM ai_analysis_records
            WHERE symbol IS NOT NULL
              AND timeframe IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM ai_analysis_records newer
                  WHERE newer.symbol = ai_analysis_records.symbol
                    AND newer.timeframe = ai_analysis_records.timeframe
                    AND (
                        newer.created_at > ai_analysis_records.created_at
                        OR (
                            newer.created_at = ai_analysis_records.created_at
                            AND newer.rowid > ai_analysis_records.rowid
                        )
                    )
              )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_analysis_records_symbol_timeframe
                ON ai_analysis_records (symbol, timeframe)
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(datasets)").fetchall()
        }
        for name in ("title", "source", "source_provider", "exchange", "last_synced_at"):
            if name not in columns:
                conn.execute(f"ALTER TABLE datasets ADD COLUMN {name} TEXT")
        conn.commit()
        conn.close()

    def upsert_strategy(self, strategy: dict[str, Any]) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO strategy_definitions
            (id, version, title, research_status, evidence_level, market, frequency, source_refs, tags, created_at)
            VALUES (:id, :version, :title, :research_status, :evidence_level, :market, :frequency, :source_refs, :tags, :created_at)
            ON CONFLICT(id) DO UPDATE SET
              version=excluded.version,
              title=excluded.title,
              research_status=excluded.research_status,
              evidence_level=excluded.evidence_level,
              market=excluded.market,
              frequency=excluded.frequency,
              source_refs=excluded.source_refs,
              tags=excluded.tags
            """,
            {
                **strategy,
                "source_refs": json.dumps(strategy.get("source_refs", []), ensure_ascii=False),
                "tags": json.dumps(strategy.get("tags", []), ensure_ascii=False),
            },
        )
        conn.commit()
        conn.close()

    def list_strategies(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM strategy_definitions ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def insert_experiment(self, exp: dict[str, Any]) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO experiments
            (id, strategy_id, status, run_id, summary, created_at)
            VALUES (:id, :strategy_id, :status, :run_id, :summary, :created_at)
            """,
            {**exp, "summary": json.dumps(exp.get("summary", {}), ensure_ascii=False)},
        )
        conn.commit()
        conn.close()

    def list_experiments(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def insert_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        bars = payload.get("bars", [])
        dataset_id = str(payload.get("id") or uuid4())
        created_at = payload.get("created_at") or datetime.now(UTC).isoformat()
        title = str(payload.get("title") or payload["symbol"]).strip()
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO datasets
            (id, symbol, title, timeframe, bar_count, first_session, last_session, created_at,
             source, source_provider, exchange, last_synced_at, bars_json)
            VALUES (:id, :symbol, :title, :timeframe, :bar_count, :first_session, :last_session,
                    :created_at, :source, :source_provider, :exchange, :last_synced_at,
                    :bars_json)
            ON CONFLICT(id) DO UPDATE SET
                symbol=excluded.symbol,
                title=excluded.title,
                timeframe=excluded.timeframe,
                bar_count=excluded.bar_count,
                first_session=excluded.first_session,
                last_session=excluded.last_session,
                source=excluded.source,
                source_provider=excluded.source_provider,
                exchange=excluded.exchange,
                last_synced_at=excluded.last_synced_at,
                bars_json=excluded.bars_json
            """,
            {
                "id": dataset_id,
                "symbol": payload["symbol"],
                "title": title,
                "timeframe": payload["timeframe"],
                "bar_count": len(bars),
                "first_session": bars[0]["session_id"] if bars else "",
                "last_session": bars[-1]["session_id"] if bars else "",
                "created_at": created_at,
                "source": payload.get("source"),
                "source_provider": payload.get("source_provider"),
                "exchange": payload.get("exchange"),
                "last_synced_at": payload.get("last_synced_at"),
                "bars_json": json.dumps(bars, ensure_ascii=False, separators=(",", ":")),
            },
        )
        conn.commit()
        conn.close()
        return self.get_dataset(dataset_id)["summary"]

    def sync_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        remote = fetch_remote_bars(payload)
        conn = self._connect()
        existing = conn.execute(
            """
            SELECT id
            FROM datasets
            WHERE symbol = ? AND timeframe = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (remote["symbol"], remote["timeframe"]),
        ).fetchone()
        conn.close()

        previous_bars: list[dict[str, Any]] = []
        dataset_id = str(
            uuid5(
                NAMESPACE_URL,
                (
                    "https://xquant.local/datasets/"
                    f"{remote['symbol'].strip().upper()}/{remote['timeframe'].strip().lower()}"
                ),
            )
        )
        created_at = datetime.now(UTC).isoformat()
        title_from_existing = ""
        if existing is not None:
            existing_record = self.get_dataset(str(existing["id"]))
            dataset_id = str(existing_record["summary"]["id"])
            created_at = str(existing_record["summary"]["created_at"])
            title_from_existing = str(existing_record["summary"].get("title") or "")
            previous_bars = list(existing_record["bars"])

        merged: dict[str, dict[str, Any]] = {
            str(bar["session_id"]): {**bar, "session_id": str(bar["session_id"])}
            for bar in previous_bars
        }
        incoming_ids = {
            str(bar["session_id"])
            for bar in remote["bars"]
            if str(bar.get("session_id") or "").strip()
        }
        inserted_count = sum(1 for session_id in incoming_ids if session_id not in merged)
        for bar in remote["bars"]:
            session_id = str(bar.get("session_id") or "").strip()
            if session_id:
                merged[session_id] = {**bar, "session_id": session_id}
        bars = sorted(
            merged.values(),
            key=lambda bar: session_sort_key(str(bar.get("session_id") or "")),
        )
        synced_at = datetime.now(UTC).isoformat()
        source = str(remote["source"])
        source_provider = str(remote["source_provider"])
        exchange = str(remote.get("exchange") or "")
        title = (
            str(remote.get("title") or "").strip()
            or resolve_instrument_title(
                str(remote["symbol"]),
                exchange=exchange,
                source=source,
            )
            or title_from_existing
            or str(remote["symbol"])
        )
        summary = self.insert_dataset(
            {
                "id": dataset_id,
                "symbol": remote["symbol"],
                "title": title,
                "timeframe": remote["timeframe"],
                "bars": bars,
                "created_at": created_at,
                "source": source,
                "source_provider": source_provider,
                "exchange": exchange or None,
                "last_synced_at": synced_at,
            }
        )
        return {
            **summary,
            "dataset": summary,
            "source": source,
            "source_provider": source_provider,
            "exchange": exchange or None,
            "inserted_count": inserted_count,
            "updated_count": len(incoming_ids) - inserted_count,
            "total_count": len(bars),
            "sync_status": "updated" if inserted_count else "unchanged",
            "synced_at": synced_at,
            "simulation_only": True,
        }

    def list_datasets(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT id, symbol, timeframe, bar_count, first_session, last_session,
                   NULLIF(title, '') AS stored_title,
                   COALESCE(NULLIF(title, ''), symbol) AS title,
                   created_at, source, source_provider, exchange, last_synced_at
            FROM datasets
            ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
        datasets = [dict(row) for row in rows]
        changed = False
        for dataset in datasets:
            symbol = str(dataset.get("symbol") or "").strip()
            stored_title = str(dataset.pop("stored_title", None) or "").strip()
            if not _title_needs_resolution(stored_title, symbol):
                continue
            title = resolve_instrument_title(
                symbol,
                exchange=str(dataset.get("exchange") or ""),
                source=str(dataset.get("source") or ""),
            )
            if not title:
                dataset["title"] = stored_title or str(dataset.get("title") or "") or symbol
                continue
            dataset["title"] = title
            if title == stored_title:
                continue
            conn.execute(
                "UPDATE datasets SET title = ? WHERE id = ?",
                (title, dataset["id"]),
            )
            changed = True
        if changed:
            conn.commit()
        conn.close()
        return datasets

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        conn = self._connect()
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if row is None:
            conn.close()
            raise KeyError(f"dataset not found: {dataset_id}")
        record = dict(row)
        symbol = str(record.get("symbol") or "").strip()
        stored_title = str(record.get("title") or "").strip()
        if _title_needs_resolution(stored_title, symbol):
            title = resolve_instrument_title(
                symbol,
                exchange=str(record.get("exchange") or ""),
                source=str(record.get("source") or ""),
            )
            if title and title != stored_title:
                record["title"] = title
                conn.execute(
                    "UPDATE datasets SET title = ? WHERE id = ?",
                    (title, dataset_id),
                )
                conn.commit()
        conn.close()
        bars = dedupe_sorted_bars(json.loads(record.pop("bars_json")))
        summary = {
            "id": record["id"],
            "symbol": record["symbol"],
            "title": record.get("title") or record["symbol"],
            "timeframe": record["timeframe"],
            "bar_count": record["bar_count"],
            "first_session": record["first_session"],
            "last_session": record["last_session"],
            "created_at": record["created_at"],
            "source": record.get("source"),
            "source_provider": record.get("source_provider"),
            "exchange": record.get("exchange"),
            "last_synced_at": record.get("last_synced_at"),
        }
        return {"summary": summary, "bars": bars}

    def delete_dataset(self, dataset_id: str) -> dict[str, Any]:
        conn = self._connect()
        cursor = conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        if cursor.rowcount == 0:
            conn.close()
            raise KeyError(f"dataset not found: {dataset_id}")
        conn.commit()
        conn.close()
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
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO ai_analysis_records
            (id, record_id, dataset_id, symbol, timeframe, status, created_at,
             duration_ms, decision_action, confidence, record_json)
            VALUES
            (:id, :record_id, :dataset_id, :symbol, :timeframe, :status, :created_at,
             :duration_ms, :decision_action, :confidence, :record_json)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                id = excluded.id,
                record_id = excluded.record_id,
                dataset_id = excluded.dataset_id,
                symbol = excluded.symbol,
                timeframe = excluded.timeframe,
                status = excluded.status,
                created_at = excluded.created_at,
                duration_ms = excluded.duration_ms,
                decision_action = excluded.decision_action,
                confidence = excluded.confidence,
                record_json = excluded.record_json
            WHERE excluded.created_at >= ai_analysis_records.created_at
            """,
            {
                **summary,
                "record_json": json.dumps(
                    stored_record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        )
        conn.commit()
        conn.close()
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
        conn = self._connect()
        if dataset_id is not None:
            rows = conn.execute(
                """
                SELECT ai_analysis_records.id, ai_analysis_records.record_id,
                       ai_analysis_records.dataset_id, ai_analysis_records.symbol,
                       ai_analysis_records.timeframe, ai_analysis_records.status,
                       ai_analysis_records.created_at, ai_analysis_records.duration_ms,
                       ai_analysis_records.decision_action, ai_analysis_records.confidence,
                       d.title AS title
                FROM ai_analysis_records
                LEFT JOIN datasets d ON d.id = ai_analysis_records.dataset_id
                WHERE ai_analysis_records.dataset_id = ?
                ORDER BY ai_analysis_records.created_at DESC, ai_analysis_records.rowid DESC
                LIMIT ? OFFSET ?
                """,
                (dataset_id, normalized_limit, normalized_offset),
            ).fetchall()
        elif symbol is not None or timeframe is not None:
            clauses: list[str] = []
            params: list[Any] = []
            if symbol is not None:
                clauses.append("ai_analysis_records.symbol = ?")
                params.append(symbol)
            if timeframe is not None:
                clauses.append("ai_analysis_records.timeframe = ?")
                params.append(timeframe)
            params.extend((normalized_limit, normalized_offset))
            rows = conn.execute(
                f"""
                SELECT ai_analysis_records.id, ai_analysis_records.record_id,
                       ai_analysis_records.dataset_id, ai_analysis_records.symbol,
                       ai_analysis_records.timeframe, ai_analysis_records.status,
                       ai_analysis_records.created_at, ai_analysis_records.duration_ms,
                       ai_analysis_records.decision_action, ai_analysis_records.confidence,
                       d.title AS title
                FROM ai_analysis_records
                LEFT JOIN datasets d ON d.id = ai_analysis_records.dataset_id
                WHERE {' AND '.join(clauses)}
                ORDER BY ai_analysis_records.created_at DESC, ai_analysis_records.rowid DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT ai_analysis_records.id, ai_analysis_records.record_id,
                       ai_analysis_records.dataset_id, ai_analysis_records.symbol,
                       ai_analysis_records.timeframe, ai_analysis_records.status,
                       ai_analysis_records.created_at, ai_analysis_records.duration_ms,
                       ai_analysis_records.decision_action, ai_analysis_records.confidence,
                       d.title AS title
                FROM ai_analysis_records
                LEFT JOIN datasets d ON d.id = ai_analysis_records.dataset_id
                ORDER BY ai_analysis_records.created_at DESC, ai_analysis_records.rowid DESC
                LIMIT ? OFFSET ?
                """,
                (normalized_limit, normalized_offset),
            ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def count_analysis_records(
        self,
        dataset_id: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> int:
        conn = self._connect()
        if dataset_id is not None:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM ai_analysis_records
                WHERE dataset_id = ?
                """,
                (dataset_id,),
            ).fetchone()
        elif symbol is not None or timeframe is not None:
            clauses: list[str] = []
            params: list[Any] = []
            if symbol is not None:
                clauses.append("symbol = ?")
                params.append(symbol)
            if timeframe is not None:
                clauses.append("timeframe = ?")
                params.append(timeframe)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM ai_analysis_records
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM ai_analysis_records
                """
            ).fetchone()
        conn.close()
        return int(row["total"]) if row is not None else 0

    def get_analysis_record(self, record_id: str) -> dict[str, Any]:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT id, record_id, dataset_id, created_at, record_json
            FROM ai_analysis_records
            WHERE id = ?
            """,
            (record_id,),
        ).fetchone()
        conn.close()
        if row is None:
            raise KeyError(f"analysis record not found: {record_id}")
        return {
            "id": row["id"],
            "record_id": row["record_id"],
            "dataset_id": row["dataset_id"],
            "created_at": row["created_at"],
            "record": json.loads(row["record_json"]),
        }

    def delete_analysis_record(self, record_id: str) -> dict[str, Any]:
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM ai_analysis_records WHERE id = ?",
            (record_id,),
        )
        if cursor.rowcount == 0:
            conn.close()
            raise KeyError(f"analysis record not found: {record_id}")
        conn.commit()
        conn.close()
        return {"deleted": True, "id": record_id}

    def get_cached_json(self, key: str) -> Any | None:
        cache_key = (str(self.path.resolve()), key)
        now = time.monotonic()
        with _PROCESS_CACHE_LOCK:
            cached = _PROCESS_CACHE.get(cache_key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= now:
                _PROCESS_CACHE.pop(cache_key, None)
                return None
            return copy.deepcopy(value)

    def set_cached_json(
        self,
        key: str,
        value: Any,
        ttl_seconds: int = 300,
    ) -> None:
        if ttl_seconds <= 0:
            return
        cache_key = (str(self.path.resolve()), key)
        expires_at = time.monotonic() + ttl_seconds
        with _PROCESS_CACHE_LOCK:
            _PROCESS_CACHE[cache_key] = (expires_at, copy.deepcopy(value))


def _normalize_record_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 50
    return max(1, min(200, parsed))


def _normalize_record_offset(offset: int) -> int:
    try:
        parsed = int(offset)
    except (TypeError, ValueError):
        parsed = 0
    return max(0, parsed)


def _record_dataset_id(record: Mapping[str, Any], dataset_id: str | None) -> str | None:
    if dataset_id is not None and str(dataset_id).strip():
        return str(dataset_id)
    snapshot = record.get("snapshot")
    if isinstance(snapshot, Mapping) and snapshot.get("dataset_id"):
        return str(snapshot["dataset_id"])
    value = record.get("dataset_id")
    return str(value) if value not in (None, "") else None


def _record_created_at(record: Mapping[str, Any]) -> str:
    value = record.get("created_at")
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
        return parsed.isoformat()
    text = str(value or "").strip()
    return text or datetime.now(UTC).isoformat()


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item) for item in value]
    return value


def _record_summary(
    record: Mapping[str, Any],
    *,
    persisted_id: str,
    dataset_id: str | None,
    created_at: str,
) -> dict[str, Any]:
    stage2 = record.get("stage2_decision")
    stage2_data = stage2 if isinstance(stage2, Mapping) else {}
    decision = stage2_data.get("decision")
    decision_data = decision if isinstance(decision, Mapping) else stage2_data
    if not decision_data:
        fallback = record.get("decision")
        decision_data = fallback if isinstance(fallback, Mapping) else {}
    action = decision_data.get("action") or decision_data.get("order_type")
    confidence = decision_data.get("confidence")
    if confidence is None:
        stage1 = record.get("stage1_diagnosis")
        stage1_data = stage1 if isinstance(stage1, Mapping) else {}
        confidence = stage1_data.get("confidence")
    record_id = record.get("id")
    return {
        "id": persisted_id,
        "record_id": str(record_id) if record_id not in (None, "") else "",
        "dataset_id": dataset_id,
        "symbol": record.get("symbol"),
        "timeframe": record.get("timeframe"),
        "status": record.get("status"),
        "created_at": created_at,
        "duration_ms": _coerce_float(record.get("duration_ms")),
        "decision_action": str(action).upper() if action not in (None, "") else None,
        "confidence": _coerce_float(confidence),
    }

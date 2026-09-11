from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from xquant.marketdata.remote import fetch_remote_bars


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
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(datasets)").fetchall()
        }
        for name in ("source", "source_provider", "exchange", "last_synced_at"):
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
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO datasets
            (id, symbol, timeframe, bar_count, first_session, last_session, created_at,
             source, source_provider, exchange, last_synced_at, bars_json)
            VALUES (:id, :symbol, :timeframe, :bar_count, :first_session, :last_session,
                    :created_at, :source, :source_provider, :exchange, :last_synced_at,
                    :bars_json)
            ON CONFLICT(id) DO UPDATE SET
                symbol=excluded.symbol,
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
        if existing is not None:
            existing_record = self.get_dataset(str(existing["id"]))
            dataset_id = str(existing_record["summary"]["id"])
            created_at = str(existing_record["summary"]["created_at"])
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
        bars = [merged[key] for key in sorted(merged)]
        synced_at = datetime.now(UTC).isoformat()
        summary = self.insert_dataset(
            {
                "id": dataset_id,
                "symbol": remote["symbol"],
                "timeframe": remote["timeframe"],
                "bars": bars,
                "created_at": created_at,
                "source": remote["source"],
                "source_provider": remote["source_provider"],
                "exchange": remote.get("exchange"),
                "last_synced_at": synced_at,
            }
        )
        return {
            **summary,
            "dataset": summary,
            "source": remote["source"],
            "source_provider": remote["source_provider"],
            "exchange": remote.get("exchange"),
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
                   created_at, source, source_provider, exchange, last_synced_at
            FROM datasets
            ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        conn = self._connect()
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        conn.close()
        if row is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        record = dict(row)
        bars = json.loads(record.pop("bars_json"))
        summary = {
            "id": record["id"],
            "symbol": record["symbol"],
            "timeframe": record["timeframe"],
            "bar_count": record["bar_count"],
            "first_session": record["first_session"],
            "last_session": record["last_session"],
            "created_at": record["created_at"],
            "source": record.get("source"),
            "source_provider": record.get("source_provider"),
            "last_synced_at": record.get("last_synced_at"),
        }
        return {"summary": summary, "bars": bars}

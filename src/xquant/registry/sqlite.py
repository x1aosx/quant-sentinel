from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


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
            """
        )
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

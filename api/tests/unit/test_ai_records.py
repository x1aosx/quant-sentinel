from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from xquant.registry.database import Database
from xquant.registry.sqlite import Database as SqliteDatabase
from xquant.storage import StorageSettings


def _record(
    record_id: str,
    created_at: str,
    *,
    symbol: str = "DEMO.RESEARCH",
    action: str = "LONG",
    confidence: float = 72.0,
    timeframe: str = "1d",
) -> dict[str, Any]:
    return {
        "id": record_id,
        "created_at": created_at,
        "status": "ok",
        "symbol": symbol,
        "timeframe": timeframe,
        "duration_ms": 123.45,
        "snapshot": {"dataset_id": "nested-dataset"},
        "stage1_diagnosis": {"confidence": 65.0},
        "stage2_decision": {
            "decision": {
                "action": action,
                "confidence": confidence,
            }
        },
        "decision_tree_layout": {
            "nodes": [{"id": "decision", "label": action}],
            "edges": [],
        },
    }


class FakePostgresStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
        *,
        fetch: str = "none",
    ) -> None:
        values = dict(params or {})
        self.statements.append((statement, values))
        if "INSERT INTO research.ai_analysis_record" not in statement:
            return
        symbol = values.get("symbol")
        timeframe = values.get("timeframe")
        existing = next(
            (
                row
                for row in self.rows.values()
                if row.get("symbol") == symbol and row.get("timeframe") == timeframe
            ),
            None,
        )
        if existing is not None and str(values["created_at"]) < str(existing["created_at"]):
            return
        if symbol is not None and timeframe is not None:
            self.rows = {
                key: row
                for key, row in self.rows.items()
                if not (
                    row.get("symbol") == symbol
                    and row.get("timeframe") == timeframe
                )
            }
        record_id = str(values["id"])
        self.rows[record_id] = {
            **values,
            "id": record_id,
            "record": json.loads(values["record"]),
        }

    def query(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        values = dict(params or {})
        self.statements.append((statement, values))
        rows = self._filtered_rows(statement, values)
        if "SELECT COUNT(*) AS total" in statement:
            return [{"total": len(rows)}]
        rows.sort(key=lambda row: (str(row["created_at"]), str(row["id"])), reverse=True)
        limit = int(values["limit"])
        offset = int(values.get("offset", 0))
        return [dict(row) for row in rows[offset : offset + limit]]

    def query_one(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        values = dict(params or {})
        if "SELECT COUNT(*) AS total" in statement:
            return {"total": len(self._filtered_rows(statement, values))}
        record_id = str(values.get("record_id") or "")
        row = self.rows.get(record_id)
        return dict(row) if row else None

    def _filtered_rows(
        self,
        statement: str,
        values: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = list(self.rows.values())
        if "dataset_id = :dataset_id" in statement:
            rows = [
                row for row in rows if row.get("dataset_id") == values.get("dataset_id")
            ]
        if "symbol = :symbol" in statement:
            rows = [row for row in rows if row.get("symbol") == values.get("symbol")]
        if "timeframe = :timeframe" in statement:
            rows = [
                row for row in rows if row.get("timeframe") == values.get("timeframe")
            ]
        return rows


def test_sqlite_analysis_records_keep_latest_per_symbol_timeframe(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "quant.db")
    first_record = _record("ai-same-millisecond", "2026-01-01T00:00:00+00:00")
    second_record = _record(
        "ai-same-millisecond",
        "2026-01-01T00:00:01+00:00",
        action="WAIT",
        confidence=80.0,
    )
    other_record = _record(
        "ai-other",
        "2026-01-01T00:00:02+00:00",
        symbol="OTHER",
        timeframe="15m",
    )

    first = database.save_analysis_record(first_record, dataset_id="dataset-a")
    second = database.save_analysis_record(second_record, dataset_id="dataset-a")
    other = database.save_analysis_record(other_record, dataset_id="dataset-b")

    assert first["id"] != second["id"]
    assert first["record_id"] == second["record_id"] == "ai-same-millisecond"
    assert first["dataset_id"] == second["dataset_id"] == "dataset-a"
    assert first["decision_action"] == "LONG"
    assert second["confidence"] == 80.0

    dataset_a_items = database.list_analysis_records(dataset_id="dataset-a")
    assert [item["id"] for item in dataset_a_items] == [second["id"]]
    assert database.list_analysis_records(dataset_id="dataset-b")[0]["id"] == other["id"]
    assert [item["id"] for item in database.list_analysis_records(symbol="DEMO.RESEARCH")] == [
        second["id"]
    ]
    assert [
        item["id"] for item in database.list_analysis_records(timeframe="15m")
    ] == [other["id"]]
    assert len(database.list_analysis_records(limit=1)) == 1
    assert len(database.list_analysis_records(limit=50)) == 2
    assert [item["id"] for item in database.list_analysis_records(limit=1, offset=1)] == [
        second["id"]
    ]
    assert database.count_analysis_records() == 2
    assert database.count_analysis_records(symbol="DEMO.RESEARCH") == 1
    assert database.count_analysis_records(timeframe="15m") == 1

    with pytest.raises(KeyError):
        database.get_analysis_record(first["id"])

    detail = database.get_analysis_record(second["id"])
    assert detail["id"] == second["id"]
    assert detail["record_id"] == "ai-same-millisecond"
    assert detail["dataset_id"] == "dataset-a"
    assert detail["record"] == second_record
    assert detail["record"]["id"] == "ai-same-millisecond"

    with pytest.raises(KeyError):
        database.get_analysis_record("missing")

    database._migrate()
    assert len(database.list_analysis_records(dataset_id="dataset-a")) == 1


def test_sqlite_analysis_records_ignore_stale_duplicate(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "quant.db")
    newest = database.save_analysis_record(
        _record("ai-newest", "2026-01-01T00:00:01+00:00"),
        dataset_id="dataset-newest",
    )
    stale = database.save_analysis_record(
        _record("ai-stale", "2026-01-01T00:00:00+00:00"),
        dataset_id="dataset-stale",
    )

    items = database.list_analysis_records()

    assert [item["id"] for item in items] == [newest["id"]]
    assert items[0]["dataset_id"] == "dataset-newest"
    with pytest.raises(KeyError):
        database.get_analysis_record(stale["id"])


def test_sqlite_migration_collapses_existing_duplicate_analysis_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ai_analysis_records (
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
        """
    )
    older = _record("legacy-old", "2026-01-01T00:00:00+00:00")
    newer = _record(
        "legacy-new",
        "2026-01-01T00:00:01+00:00",
        action="WAIT",
        confidence=81.0,
    )
    for persisted_id, dataset_id, record in (
        ("legacy-new-row", "dataset-new", newer),
        ("legacy-old-row", "dataset-old", older),
    ):
        conn.execute(
            """
            INSERT INTO ai_analysis_records
            (id, record_id, dataset_id, symbol, timeframe, status, created_at,
             duration_ms, decision_action, confidence, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persisted_id,
                record["id"],
                dataset_id,
                record["symbol"],
                record["timeframe"],
                record["status"],
                record["created_at"],
                record["duration_ms"],
                "WAIT" if record is newer else "LONG",
                81.0 if record is newer else 72.0,
                json.dumps(record),
            ),
        )
    conn.commit()
    conn.close()

    database = SqliteDatabase(path)
    items = database.list_analysis_records()

    assert [item["id"] for item in items] == ["legacy-new-row"]
    assert items[0]["dataset_id"] == "dataset-new"
    with pytest.raises(KeyError):
        database.get_analysis_record("legacy-old-row")


def test_sqlite_analysis_records_include_dataset_title(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "quant.db")
    database.insert_dataset(
        {
            "id": "dataset-titled",
            "symbol": "600519.SH",
            "title": "贵州茅台",
            "timeframe": "1d",
            "bars": [],
        }
    )
    titled = database.save_analysis_record(
        _record("ai-titled", "2026-01-04T00:00:00+00:00", symbol="600519.SH"),
        dataset_id="dataset-titled",
    )
    orphan = database.save_analysis_record(
        _record("ai-orphan", "2026-01-04T00:00:01+00:00", symbol="000001.SZ"),
        dataset_id="dataset-missing",
    )
    unattached = database.save_analysis_record(
        _record("ai-unattached", "2026-01-04T00:00:02+00:00", symbol="000002.SZ"),
        dataset_id=None,
    )

    titled_items = database.list_analysis_records(dataset_id="dataset-titled")
    assert [item["id"] for item in titled_items] == [titled["id"]]
    assert titled_items[0]["title"] == "贵州茅台"

    orphan_items = database.list_analysis_records(dataset_id="dataset-missing")
    assert [item["id"] for item in orphan_items] == [orphan["id"]]
    assert orphan_items[0]["title"] is None

    unattached_items = database.list_analysis_records(symbol="000002.SZ")
    assert [item["id"] for item in unattached_items] == [unattached["id"]]
    assert unattached_items[0]["title"] is None

    assert database.list_analysis_records(symbol="600519.SH")[0]["title"] == "贵州茅台"
    titles_by_id = {
        item["id"]: item["title"] for item in database.list_analysis_records()
    }
    assert titles_by_id == {
        titled["id"]: "贵州茅台",
        orphan["id"]: None,
        unattached["id"]: None,
    }
    assert database.count_analysis_records() == 3


def test_postgres_analysis_record_sql_path_with_fake_store() -> None:
    postgres = FakePostgresStore()
    database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=postgres,  # type: ignore[arg-type]
        redis_store=None,
        influx=None,  # type: ignore[arg-type]
    )
    record = _record("ai-postgres", "2026-01-02T00:00:00+00:00")

    saved = database.save_analysis_record(record, dataset_id="dataset-pg")
    replacement = _record(
        "ai-postgres-latest",
        "2026-01-02T00:00:01+00:00",
        action="WAIT",
        confidence=88.0,
    )
    replaced = database.save_analysis_record(replacement, dataset_id="dataset-pg-latest")
    stale = _record("ai-postgres-stale", "2026-01-02T00:00:00+00:00")
    database.save_analysis_record(stale, dataset_id="dataset-pg-stale")
    items = database.list_analysis_records(dataset_id="dataset-pg-latest", limit=50)
    detail = database.get_analysis_record(replaced["id"])

    assert saved["dataset_id"] == "dataset-pg"
    assert replaced["dataset_id"] == "dataset-pg-latest"
    # FakePostgresStore 不执行真实 SQL，LEFT JOIN 取不到数据集标题，title 恒为 None。
    assert items == [{**replaced, "title": None}]
    assert detail["record"] == replacement
    assert detail["dataset_id"] == "dataset-pg-latest"
    assert database.list_analysis_records(dataset_id="dataset-pg") == []
    assert database.count_analysis_records() == 1
    assert database.count_analysis_records(dataset_id="dataset-pg-latest") == 1
    with pytest.raises(KeyError):
        database.get_analysis_record(saved["id"])
    insert_statements = [
        statement
        for statement, _params in postgres.statements
        if "INSERT INTO research.ai_analysis_record" in statement
    ]
    query_statements = [
        statement
        for statement, _params in postgres.statements
        if "FROM research.ai_analysis_record" in statement
    ]
    assert insert_statements
    assert "CAST(:record AS jsonb)" in insert_statements[0]
    assert "ON CONFLICT (symbol, timeframe) DO UPDATE" in insert_statements[0]
    assert "WHERE EXCLUDED.created_at >= current_record.created_at" in insert_statements[0]
    assert query_statements
    assert any(
        "LEFT JOIN research.dataset" in statement for statement in query_statements
    )
    assert any("d.title AS title" in statement for statement in query_statements)

    with pytest.raises(KeyError):
        database.get_analysis_record("missing")


def test_analysis_records_replace_non_finite_floats_with_none(tmp_path: Path) -> None:
    record = _record("ai-non-finite", "2026-01-03T00:00:00+00:00")
    record["duration_ms"] = float("inf")
    record["snapshot"]["nan_value"] = float("nan")
    record["snapshot"]["nested"] = [float("inf"), {"negative": float("-inf")}]
    record["stage2_decision"]["decision"]["confidence"] = float("nan")

    sqlite_database = SqliteDatabase(tmp_path / "quant.db")
    saved = sqlite_database.save_analysis_record(record, dataset_id="dataset-a")
    detail = sqlite_database.get_analysis_record(saved["id"])

    assert saved["duration_ms"] is None
    assert saved["confidence"] is None
    assert detail["record"]["snapshot"]["nan_value"] is None
    assert detail["record"]["snapshot"]["nested"] == [None, {"negative": None}]
    assert math.isnan(record["snapshot"]["nan_value"])

    postgres = FakePostgresStore()
    postgres_database = Database(
        settings=StorageSettings(storage_backend="postgres", auto_migrate=False),
        postgres=postgres,  # type: ignore[arg-type]
        redis_store=None,
        influx=None,  # type: ignore[arg-type]
    )
    postgres_database.save_analysis_record(record, dataset_id="dataset-a")
    serialized_record = next(
        params["record"]
        for statement, params in postgres.statements
        if "INSERT INTO research.ai_analysis_record" in statement
    )
    assert "NaN" not in serialized_record
    assert "Infinity" not in serialized_record

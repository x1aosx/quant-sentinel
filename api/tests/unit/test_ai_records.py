from __future__ import annotations

import json
import math
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
) -> dict[str, Any]:
    return {
        "id": record_id,
        "created_at": created_at,
        "status": "ok",
        "symbol": symbol,
        "timeframe": "1d",
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
        record_id = str(values["id"])
        self.rows[record_id] = {
            **values,
            "id": record_id,
            "record": json.loads(values["record"]),
        }

    def query(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        values = dict(params or {})
        self.statements.append((statement, values))
        rows = list(self.rows.values())
        if "WHERE dataset_id = :dataset_id" in statement:
            rows = [
                row for row in rows if row.get("dataset_id") == values.get("dataset_id")
            ]
        rows.sort(key=lambda row: (str(row["created_at"]), str(row["id"])), reverse=True)
        limit = int(values["limit"])
        return [dict(row) for row in rows[:limit]]

    def query_one(
        self,
        statement: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        record_id = str((params or {}).get("record_id") or "")
        row = self.rows.get(record_id)
        return dict(row) if row else None


def test_sqlite_analysis_records_save_filter_detail_and_duplicate_ids(tmp_path: Path) -> None:
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
    assert [item["id"] for item in dataset_a_items] == [second["id"], first["id"]]
    assert database.list_analysis_records(dataset_id="dataset-b")[0]["id"] == other["id"]
    assert len(database.list_analysis_records(limit=1)) == 1
    assert len(database.list_analysis_records(limit=0)) == 1

    detail = database.get_analysis_record(first["id"])
    assert detail["id"] == first["id"]
    assert detail["record_id"] == "ai-same-millisecond"
    assert detail["dataset_id"] == "dataset-a"
    assert detail["record"] == first_record
    assert detail["record"]["id"] == "ai-same-millisecond"

    with pytest.raises(KeyError):
        database.get_analysis_record("missing")

    database._migrate()
    assert len(database.list_analysis_records(dataset_id="dataset-a")) == 2


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
    items = database.list_analysis_records(dataset_id="dataset-pg", limit=50)
    detail = database.get_analysis_record(saved["id"])

    assert saved["dataset_id"] == "dataset-pg"
    assert items == [saved]
    assert detail["record"] == record
    assert detail["dataset_id"] == "dataset-pg"
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
    assert query_statements

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

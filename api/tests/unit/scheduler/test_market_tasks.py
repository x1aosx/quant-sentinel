from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from xquant.marketdata.tasks import (
    DatasetSyncHandler,
    MarketWatchlistPlanner,
    WatchlistSyncHandler,
)
from xquant.scheduler.domain import TaskContext, TaskExecution


class _Database:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def sync_dataset(self, payload: dict) -> dict:
        self.calls.append(dict(payload))
        return {
            "inserted_count": 10,
            "updated_count": 2,
            "total_count": 12,
            "symbol": payload["symbol"],
        }


def _context(params: dict) -> TaskContext:
    now = datetime(2026, 9, 15, 9, 31, tzinfo=UTC)
    return TaskContext(
        task_name="test",
        execution_id="execution-1",
        schedule_id=None,
        scheduled_at=now,
        started_at=now,
        attempt=1,
        params=params,
        trace_id="trace-1",
    )


def test_dataset_sync_handler_uses_existing_database_service() -> None:
    database = _Database()

    result = asyncio.run(
        DatasetSyncHandler(database).execute(
            _context({"source": "yfinance", "symbol": "600000", "timeframe": "1d"})
        )
    )

    assert result.success is True
    assert result.metrics["total_count"] == 12
    assert database.calls[0]["symbol"] == "600000"


def test_watchlist_sync_handler_normalizes_batch_entries() -> None:
    database = _Database()

    result = asyncio.run(
        WatchlistSyncHandler(database).execute(
            _context(
                {
                    "symbols": [
                        {"symbol": "600000"},
                        {"source": "yfinance", "symbol": "000001", "timeframe": "1d"},
                    ]
                }
            )
        )
    )

    assert result.success is True
    assert result.metrics == {"symbol_count": 2, "record_count": 24}
    assert database.calls[0]["lookback"] == 500
    assert database.calls[1]["source"] == "yfinance"


def test_market_watchlist_planner_builds_dependent_summary_node() -> None:
    execution = TaskExecution(
        id="plan-root",
        task_name="market.symbol.sync",
        scheduled_at=datetime(2026, 9, 15, 9, 31, tzinfo=UTC),
        params={
            "symbols": [
                {"symbol": "600000"},
                {"symbol": "000001"},
            ]
        },
        trace_id="trace-plan",
    )

    plan = asyncio.run(MarketWatchlistPlanner().plan(execution))
    order = plan.topological_order()

    assert [node.key for node in order] == ["dataset-0", "dataset-1", "summary"]
    assert order[-1].depends_on == ("dataset-0", "dataset-1")

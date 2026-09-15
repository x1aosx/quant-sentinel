from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from xquant.scheduler.application import TaskRegistry
from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    RetryPolicy,
    TaskContext,
    TaskDefinition,
    TaskResult,
)


class DatasetSyncHandler:
    """Adapter from scheduler task context to the existing dataset service."""

    def __init__(self, database: Any) -> None:
        self.database = database

    async def execute(self, context: TaskContext) -> TaskResult:
        payload = dict(context.params)
        result = await asyncio.to_thread(self.database.sync_dataset, payload)
        return TaskResult(
            success=True,
            data={"dataset": result},
            metrics={
                "inserted_count": int(result.get("inserted_count") or 0),
                "updated_count": int(result.get("updated_count") or 0),
                "total_count": int(result.get("total_count") or 0),
            },
        )


class WatchlistSyncHandler:
    """Sync a configured list of dataset specifications."""

    def __init__(self, database: Any) -> None:
        self.database = database

    async def execute(self, context: TaskContext) -> TaskResult:
        raw_items = context.params.get("symbols")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise TypeError("market.symbol.sync params.symbols must be a list")
        items = [_normalize_item(item) for item in raw_items]
        if not items:
            raise ValueError("market.symbol.sync params.symbols cannot be empty")

        results = await asyncio.gather(
            *(asyncio.to_thread(self.database.sync_dataset, item) for item in items)
        )
        return TaskResult(
            success=True,
            data={"datasets": results},
            metrics={
                "symbol_count": len(items),
                "record_count": sum(int(item.get("total_count") or 0) for item in results),
            },
        )


def register_market_tasks(registry: TaskRegistry, database: Any) -> None:
    """Register the first concrete consumers without coupling scheduler to market data."""

    retry_policy = RetryPolicy(
        max_attempts=3,
        strategy="exponential",
        initial_delay_seconds=5,
        max_delay_seconds=60,
        jitter=True,
        no_retry_on=("ValueError",),
    )
    registry.register(
        TaskDefinition(
            name="market.daily.sync",
            handler="DatasetSyncHandler",
            description="同步单个品种的已收盘行情数据集",
            timeout_seconds=300,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="market-data",
            priority=4,
        ),
        DatasetSyncHandler(database),
    )
    registry.register(
        TaskDefinition(
            name="market.symbol.sync",
            handler="WatchlistSyncHandler",
            description="按配置批量同步多个品种数据集",
            timeout_seconds=900,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="batch",
            priority=6,
        ),
        WatchlistSyncHandler(database),
    )


def _normalize_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("each symbol entry must be an object")
    item = dict(value)
    if not str(item.get("symbol") or "").strip():
        raise ValueError("each symbol entry requires symbol")
    item.setdefault("timeframe", "1d")
    item.setdefault("source", "yfinance")
    item.setdefault("lookback", 500)
    item.setdefault("adjust", "qfq")
    return item


__all__ = ["DatasetSyncHandler", "WatchlistSyncHandler", "register_market_tasks"]

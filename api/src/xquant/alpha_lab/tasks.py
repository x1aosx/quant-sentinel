from __future__ import annotations

import asyncio
from typing import Any

from xquant.scheduler.application import TaskRegistry
from xquant.scheduler.domain import (
    ConcurrencyPolicy,
    RetryPolicy,
    TaskContext,
    TaskDefinition,
    TaskResult,
)


class AlphaTrainingHandler:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def execute(self, context: TaskContext) -> TaskResult:
        result = await asyncio.to_thread(
            self.runtime.training.create_run,
            dict(context.params),
        )
        return TaskResult(
            success=True,
            data={"run": result},
            metrics={"step": int(result.get("step") or 0)},
        )


class AlphaBacktestHandler:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def execute(self, context: TaskContext) -> TaskResult:
        result = await asyncio.to_thread(
            self.runtime.backtest.run,
            dict(context.params),
        )
        return TaskResult(
            success=True,
            data={"backtest": result},
            metrics={
                "trade_count": int(result.get("metrics", {}).get("trade_count") or 0),
            },
        )


class AlphaRealtimeEvaluateHandler:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def execute(self, context: TaskContext) -> TaskResult:
        result = await asyncio.to_thread(
            self.runtime.realtime.evaluate,
            dict(context.params),
        )
        return TaskResult(
            success=True,
            data=result,
            metrics={
                "evaluated": int(result.get("evaluated") or 0),
                "generated": int(result.get("generated") or 0),
                "error_count": len(result.get("errors") or []),
            },
        )


def register_alpha_tasks(registry: TaskRegistry, runtime: Any) -> None:
    retry_policy = RetryPolicy(
        max_attempts=2,
        strategy="exponential",
        initial_delay_seconds=10,
        max_delay_seconds=120,
        jitter=True,
        no_retry_on=("ValueError", "TypeError", "KeyError"),
    )
    registry.register(
        TaskDefinition(
            name="alpha.training.run",
            handler="AlphaTrainingHandler",
            description="启动 AlphaLab 因子挖掘训练任务",
            timeout_seconds=900,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="model",
            priority=3,
        ),
        AlphaTrainingHandler(runtime),
    )
    registry.register(
        TaskDefinition(
            name="alpha.backtest.run",
            handler="AlphaBacktestHandler",
            description="执行 AlphaLab 策略回测",
            timeout_seconds=600,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="backtest",
            priority=4,
        ),
        AlphaBacktestHandler(runtime),
    )
    registry.register(
        TaskDefinition(
            name="alpha.realtime.evaluate",
            handler="AlphaRealtimeEvaluateHandler",
            description="评估 AlphaLab 已收盘实时策略信号",
            timeout_seconds=300,
            retry_policy=retry_policy,
            concurrency_policy=ConcurrencyPolicy.FORBID,
            queue="realtime",
            priority=2,
        ),
        AlphaRealtimeEvaluateHandler(runtime),
    )


__all__ = [
    "AlphaBacktestHandler",
    "AlphaRealtimeEvaluateHandler",
    "AlphaTrainingHandler",
    "register_alpha_tasks",
]

from __future__ import annotations

from typing import Any

from xquant.alpha_lab.runtime import AlphaLabRuntime, register_alpha_tasks
from xquant.intelligence import register_intelligence_tasks
from xquant.marketdata.tasks import register_market_tasks
from xquant.scheduler.application import TaskRegistry


def register_all_tasks(
    registry: TaskRegistry,
    database: Any,
    *,
    intelligence_service: Any | None = None,
    alpha_lab_runtime: AlphaLabRuntime | None = None,
) -> None:
    """Register every available task from the application composition root."""

    register_market_tasks(registry, database)
    if intelligence_service is not None:
        register_intelligence_tasks(registry, intelligence_service)
    if alpha_lab_runtime is not None:
        register_alpha_tasks(registry, alpha_lab_runtime)


__all__ = ["register_all_tasks"]

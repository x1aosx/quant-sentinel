from __future__ import annotations

from typing import Any, Protocol


class TrainingPort(Protocol):
    def list_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_run(self, run_id: str) -> dict[str, Any]:
        ...

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        ...


class BacktestPort(Protocol):
    def list_results(self, *, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class StrategyPort(Protocol):
    def list_artifacts(self) -> list[dict[str, Any]]:
        ...

    def get_artifact(
        self,
        strategy_id: str,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        ...

    def import_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class RealtimePort(Protocol):
    def list_watches(self) -> list[dict[str, Any]]:
        ...

    def create_watch(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_watch(self, watch_id: str) -> bool:
        ...

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def list_signals(self, *, limit: int = 200) -> list[dict[str, Any]]:
        ...


class AlphaLabService:
    """Application facade that keeps API routes independent of implementation details."""

    def __init__(
        self,
        *,
        training: TrainingPort,
        backtest: BacktestPort,
        strategies: StrategyPort,
        realtime: RealtimePort,
    ) -> None:
        self.training = training
        self.backtest = backtest
        self.strategies = strategies
        self.realtime = realtime

    def overview(self) -> dict[str, Any]:
        runs = self.training.list_runs(limit=20)
        strategies = self.strategies.list_artifacts()
        backtests = self.backtest.list_results(limit=20)
        watches = self.realtime.list_watches()
        return {
            "training": {
                "total": len(runs),
                "running": sum(
                    1 for item in runs if str(item.get("status") or "") == "RUNNING"
                ),
                "recent": runs,
            },
            "strategies": {
                "total": len(strategies),
                "production": sum(
                    1
                    for item in strategies
                    if str(item.get("status") or "") == "production"
                ),
                "recent": strategies[:20],
            },
            "backtests": {
                "total": len(backtests),
                "recent": backtests,
            },
            "realtime": {
                "watch_count": len(watches),
                "watches": watches,
            },
        }

    def list_training_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.training.list_runs(limit=limit)

    def create_training_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.training.create_run(payload)

    def get_training_run(self, run_id: str) -> dict[str, Any]:
        return self.training.get_run(run_id)

    def cancel_training_run(self, run_id: str) -> dict[str, Any]:
        return self.training.cancel_run(run_id)

    def list_strategies(self) -> list[dict[str, Any]]:
        return self.strategies.list_artifacts()

    def get_strategy(
        self,
        strategy_id: str,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        return self.strategies.get_artifact(strategy_id, version=version)

    def import_strategy(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.strategies.import_artifact(payload)

    def list_backtests(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.backtest.list_results(limit=limit)

    def run_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.backtest.run(payload)

    def list_realtime_watches(self) -> list[dict[str, Any]]:
        return self.realtime.list_watches()

    def create_realtime_watch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.realtime.create_watch(payload)

    def delete_realtime_watch(self, watch_id: str) -> bool:
        return self.realtime.delete_watch(watch_id)

    def evaluate_realtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.realtime.evaluate(payload)

    def list_realtime_signals(
        self,
        *,
        limit: int = 200,
        watch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.realtime.list_signals(limit=limit, watch_id=watch_id)

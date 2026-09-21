from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..config import AlphaLabSettings
from ..errors import AlphaLabError

router = APIRouter(prefix="/alphalab", tags=["alpha-lab"])


def get_alpha_lab_service(request: Request) -> Any:
    service = getattr(request.app.state, "alpha_lab_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="AlphaLab 模块未启用")
    return service


AlphaLabServiceDep = Annotated[Any, Depends(get_alpha_lab_service)]


@router.get("/overview")
def overview(service: AlphaLabServiceDep) -> dict[str, Any]:
    return service.overview()


@router.get("/training/runs")
def list_training_runs(
    service: AlphaLabServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": service.list_training_runs(limit=limit)}


@router.post("/training/runs")
def create_training_run(
    payload: dict[str, Any],
    service: AlphaLabServiceDep,
) -> dict[str, Any]:
    try:
        return service.create_training_run(payload)
    except (KeyError, TypeError, ValueError, AlphaLabError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/training/runs/{run_id}")
def get_training_run(run_id: str, service: AlphaLabServiceDep) -> dict[str, Any]:
    try:
        return service.get_training_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="训练任务不存在") from exc


@router.post("/training/runs/{run_id}/cancel")
def cancel_training_run(run_id: str, service: AlphaLabServiceDep) -> dict[str, Any]:
    try:
        return service.cancel_training_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="训练任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/strategies")
def list_strategies(service: AlphaLabServiceDep) -> dict[str, Any]:
    return {"items": service.list_strategies()}


@router.get("/strategies/{strategy_id}")
def get_strategy(
    strategy_id: str,
    service: AlphaLabServiceDep,
    version: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return service.get_strategy(strategy_id, version=version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="策略不存在") from exc


@router.post("/strategies/import")
def import_strategy(
    payload: dict[str, Any],
    service: AlphaLabServiceDep,
) -> dict[str, Any]:
    try:
        return service.import_strategy(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/backtests")
def list_backtests(
    service: AlphaLabServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return {"items": service.list_backtests(limit=limit)}


@router.post("/backtests")
def run_backtest(
    payload: dict[str, Any],
    service: AlphaLabServiceDep,
) -> dict[str, Any]:
    try:
        return service.run_backtest(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/realtime/watches")
def list_realtime_watches(service: AlphaLabServiceDep) -> dict[str, Any]:
    return {"items": service.list_realtime_watches()}


@router.post("/realtime/watches")
def create_realtime_watch(
    payload: dict[str, Any],
    service: AlphaLabServiceDep,
) -> dict[str, Any]:
    try:
        return service.create_realtime_watch(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/realtime/watches/{watch_id}")
def delete_realtime_watch(
    watch_id: str,
    service: AlphaLabServiceDep,
) -> dict[str, bool]:
    deleted = service.delete_realtime_watch(watch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="实时监控任务不存在")
    return {"deleted": True}


@router.post("/realtime/evaluate")
def evaluate_realtime(
    payload: dict[str, Any],
    service: AlphaLabServiceDep,
) -> dict[str, Any]:
    try:
        return service.evaluate_realtime(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/realtime/signals")
def list_realtime_signals(
    service: AlphaLabServiceDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    watch_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return {
        "items": service.list_realtime_signals(
            limit=limit,
            watch_id=watch_id,
        )
    }


def alpha_lab_enabled(settings: AlphaLabSettings) -> bool:
    return settings.enabled

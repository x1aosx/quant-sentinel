from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from xquant.marketdata.sync_schedules import DatasetSyncScheduleService
from xquant.marketdata.synthetic import generate_synthetic_bars
from xquant.registry import Database
from xquant.scheduler.runtime import SchedulerRuntime

from ..dependencies import get_database, get_dataset_or_404
from ..validation import normalize_bars
from .scheduler import get_scheduler_runtime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["datasets"])

SchedulerDep = Annotated[SchedulerRuntime, Depends(get_scheduler_runtime)]


@router.post("/datasets")
def create_dataset(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    try:
        symbol = str(payload.get("symbol") or "").strip()
        timeframe = str(payload.get("timeframe") or "1d").strip() or "1d"
        if not symbol:
            raise HTTPException(status_code=400, detail="品种名称不能为空")
        bars = normalize_bars(payload.get("bars"))
        if len(bars) < 60:
            raise HTTPException(status_code=400, detail="数据不足：至少需要 60 根K线")
        dataset = db.insert_dataset(
            {
                "symbol": symbol,
                "title": str(payload.get("title") or symbol).strip(),
                "timeframe": timeframe,
                "bars": bars,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dataset


@router.post("/datasets/sample")
def create_sample_dataset(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    timeframe = str(payload.get("timeframe") or "1d").strip() or "1d"
    model_bars = generate_synthetic_bars("DEMO.RESEARCH", n=180, seed=42)
    bars = normalize_bars(
        {
            "bars": [
                {
                    "session_id": bar.session_id,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume_shares,
                }
                for bar in model_bars
            ]
        }
    )
    return db.insert_dataset(
        {
            "symbol": "DEMO.RESEARCH",
            "title": "研究示例数据",
            "timeframe": timeframe,
            "bars": bars,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )


@router.post("/datasets/remote")
async def import_remote_dataset(
    request: Request,
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _sync_remote_dataset(request, db, payload)


@router.post("/datasets/remote/sync")
async def sync_remote_dataset(
    request: Request,
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _sync_remote_dataset(request, db, payload)


async def _sync_remote_dataset(
    request: Request,
    db: Database,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    body = payload or {}
    try:
        result = await asyncio.to_thread(db.sync_dataset, body)
        if int(result.get("total_count") or result.get("bar_count") or 0) < 60:
            raise HTTPException(status_code=400, detail="远程行情数据不足：至少需要 60 根已收盘K线")
    except HTTPException:
        raise
    except (TypeError, ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _adopt_dataset_sync_symbol(request, db, result)
    return result


async def _adopt_dataset_sync_symbol(
    request: Request,
    db: Database,
    result: dict[str, Any],
) -> None:
    """已开启自动同步的标的新增周期时，自动把新数据集纳入同步。"""

    runtime = getattr(request.app.state, "scheduler_runtime", None)
    if runtime is None:
        return
    symbol = str(result.get("symbol") or "").strip()
    if not symbol:
        return
    try:
        await DatasetSyncScheduleService(db, runtime).adopt_symbol(symbol)
    except Exception:  # pragma: no cover - 自动纳入失败不能影响同步本身
        logger.warning("数据集 %s 自动纳入同步计划失败", symbol, exc_info=True)


@router.put("/datasets/{dataset_id}/sync-schedule")
async def set_dataset_sync_schedule(
    dataset_id: str,
    db: Annotated[Database, Depends(get_database)],
    runtime: SchedulerDep,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    service = DatasetSyncScheduleService(db, runtime)
    try:
        return await service.set_dataset_sync(
            dataset_id,
            enabled=bool(body.get("enabled", True)),
            interval_seconds=body.get("interval_seconds"),
            lookback=body.get("lookback"),
            adjust=body.get("adjust"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据集不存在") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datasets/sync-schedules/reconcile")
async def reconcile_dataset_sync_schedules(
    db: Annotated[Database, Depends(get_database)],
    runtime: SchedulerDep,
) -> dict[str, Any]:
    return await DatasetSyncScheduleService(db, runtime).reconcile()


@router.get("/datasets")
def list_datasets(db: Annotated[Database, Depends(get_database)]) -> dict[str, Any]:
    return {"items": db.list_datasets()}


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str, db: Annotated[Database, Depends(get_database)]
) -> dict[str, Any]:
    return get_dataset_or_404(db, dataset_id)["summary"]


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    request: Request,
    dataset_id: str,
    db: Annotated[Database, Depends(get_database)],
) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(db.delete_dataset, dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据集不存在") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="删除行情数据失败") from exc
    await _drop_dataset_sync_schedule(request, db, dataset_id)
    return result


async def _drop_dataset_sync_schedule(
    request: Request,
    db: Database,
    dataset_id: str,
) -> None:
    runtime = getattr(request.app.state, "scheduler_runtime", None)
    if runtime is None:
        return
    try:
        await DatasetSyncScheduleService(db, runtime).delete_for_dataset(dataset_id)
    except Exception:  # pragma: no cover - 清理失败不能影响删除本身
        logger.warning("清理数据集 %s 的同步计划失败", dataset_id, exc_info=True)

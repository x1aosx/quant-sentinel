from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from xquant.marketdata.synthetic import generate_synthetic_bars
from xquant.registry import Database

from ..dependencies import get_database, get_dataset_or_404
from ..validation import normalize_bars

router = APIRouter(tags=["datasets"])


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
def import_remote_dataset(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return sync_remote_dataset(db, payload)


@router.post("/datasets/remote/sync")
def sync_remote_dataset(
    db: Annotated[Database, Depends(get_database)],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    try:
        result = db.sync_dataset(payload)
        if int(result.get("total_count") or result.get("bar_count") or 0) < 60:
            raise HTTPException(status_code=400, detail="远程行情数据不足：至少需要 60 根已收盘K线")
    except HTTPException:
        raise
    except (TypeError, ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/datasets")
def list_datasets(db: Annotated[Database, Depends(get_database)]) -> dict[str, Any]:
    return {"items": db.list_datasets()}


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str, db: Annotated[Database, Depends(get_database)]
) -> dict[str, Any]:
    return get_dataset_or_404(db, dataset_id)["summary"]


@router.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str, db: Annotated[Database, Depends(get_database)]
) -> dict[str, Any]:
    try:
        return db.delete_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据集不存在") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="删除行情数据失败") from exc

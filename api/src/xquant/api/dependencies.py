from __future__ import annotations

from fastapi import HTTPException, Request

from xquant.ai.coordinator import MonitorManager
from xquant.registry import Database
from xquant.storage import StorageSettings
from xquant.system_config import SystemConfigStore


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_storage_settings(request: Request) -> StorageSettings:
    return request.app.state.settings


def get_system_config(request: Request) -> SystemConfigStore:
    return request.app.state.system_config


def get_monitor(request: Request) -> MonitorManager:
    return request.app.state.monitor


def get_dataset_or_404(db: Database, dataset_id: str) -> dict[str, object]:
    try:
        return db.get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据集不存在") from exc


def get_stock_timeframe_dataset_or_404(
    db: Database,
    *,
    dataset_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict[str, object]:
    normalized_dataset_id = str(dataset_id or "").strip()
    if normalized_dataset_id:
        return get_dataset_or_404(db, normalized_dataset_id)

    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    normalized_timeframe = str(timeframe or "1d").strip().lower() or "1d"
    matches = [
        item
        for item in db.list_datasets()
        if str(item.get("symbol") or "").strip().upper() == normalized_symbol
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="股票不存在或尚未同步行情")
    dataset = next(
        (
            item
            for item in matches
            if str(item.get("timeframe") or "").strip().lower() == normalized_timeframe
        ),
        None,
    )
    if dataset is None:
        raise HTTPException(
            status_code=404,
            detail=f"股票 {normalized_symbol} 缺少 {normalized_timeframe} 周期数据",
        )
    return get_dataset_or_404(db, str(dataset["id"]))

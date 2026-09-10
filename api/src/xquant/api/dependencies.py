from __future__ import annotations

from fastapi import HTTPException, Request

from xquant.registry import Database
from xquant.storage import StorageSettings


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_storage_settings(request: Request) -> StorageSettings:
    return request.app.state.settings


def get_dataset_or_404(db: Database, dataset_id: str) -> dict[str, object]:
    try:
        return db.get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="数据集不存在") from exc

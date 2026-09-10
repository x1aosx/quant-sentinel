from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from xquant.registry import Database
from xquant.storage import StorageSettings

from ..dependencies import get_database, get_storage_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(
    db: Annotated[Database, Depends(get_database)],
    settings: Annotated[StorageSettings, Depends(get_storage_settings)],
) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "0.2.0",
        "mode": "local_research",
        "storage_backend": settings.storage_backend,
        "dataset_count": len(db.list_datasets()),
    }


@router.get("/health/storage")
def storage_health(db: Annotated[Database, Depends(get_database)]) -> dict[str, Any]:
    if not hasattr(db, "storage_health"):
        return {"status": "ok", "services": {"legacy_sqlite": {"status": "ok"}}}
    return {"status": "ok", "services": db.storage_health()}

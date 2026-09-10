from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from xquant.registry import Database

from ..dependencies import get_database

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard(db: Annotated[Database, Depends(get_database)]) -> dict[str, Any]:
    datasets = db.list_datasets()
    return {
        "status": "ok",
        "mode": "local_research",
        "dataset_count": len(datasets),
        "latest_dataset": datasets[0] if datasets else None,
    }

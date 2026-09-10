from __future__ import annotations

from fastapi import APIRouter

from .analysis import router as analysis_router
from .dashboard import router as dashboard_router
from .datasets import router as datasets_router
from .health import router as health_router
from .marketdata import router as marketdata_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(analysis_router)
api_router.include_router(dashboard_router)
api_router.include_router(datasets_router)
api_router.include_router(health_router)
api_router.include_router(marketdata_router)

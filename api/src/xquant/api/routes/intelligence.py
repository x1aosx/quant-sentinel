from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder

router = APIRouter(tags=["intelligence"])


def get_intelligence_service(request: Request) -> Any:
    service = getattr(request.app.state, "intelligence_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="市场情报模块未启用")
    return service


IntelligenceDep = Annotated[Any, Depends(get_intelligence_service)]


@router.get("/intelligence/overview")
def get_intelligence_overview(service: IntelligenceDep) -> dict[str, Any]:
    return jsonable_encoder(service.overview())


@router.post("/intelligence/run")
async def run_intelligence_pipeline(
    service: IntelligenceDep,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    try:
        result = await service.run(process_only=bool(body.get("process_only", False)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return jsonable_encoder(result)


@router.get("/intelligence/events")
def list_intelligence_events(
    service: IntelligenceDep,
    limit: int = Query(default=50, ge=1, le=500),
    hot_only: bool = Query(default=False),
) -> dict[str, Any]:
    items = service.list_events(limit=limit, hot_only=hot_only)
    return {"items": jsonable_encoder(items), "count": len(items)}


@router.get("/intelligence/information")
def list_raw_information(
    service: IntelligenceDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    items = service.list_information(limit=limit)
    return {"items": jsonable_encoder(items), "count": len(items)}


@router.get("/intelligence/sources")
def list_information_sources(service: IntelligenceDep) -> dict[str, Any]:
    items = service.list_sources()
    return {"items": jsonable_encoder(items), "count": len(items)}


@router.get("/themes")
def list_themes(
    service: IntelligenceDep,
    limit: int = Query(default=50, ge=1, le=500),
    category: str = Query(default="all"),
) -> dict[str, Any]:
    try:
        items = service.list_themes(limit=limit, category=category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": jsonable_encoder(items), "count": len(items)}


@router.get("/brief/morning")
def get_morning_brief(service: IntelligenceDep) -> dict[str, Any]:
    return {"brief": jsonable_encoder(service.get_morning_brief())}

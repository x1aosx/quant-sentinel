from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder

router = APIRouter(tags=["discovery"])


def get_discovery_service(request: Request) -> Any:
    service = getattr(request.app.state, "discovery_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="机会发现模块未启用")
    return service


DiscoveryDep = Annotated[Any, Depends(get_discovery_service)]


@router.get("/discovery/candidates")
def list_discovery_candidates(
    service: DiscoveryDep,
    state: str = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    return jsonable_encoder(service.list_candidates(state=state, limit=limit))


@router.post("/discovery/refresh")
def refresh_discovery_candidates(
    service: DiscoveryDep,
    expand_universe: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        result = service.refresh(expand_universe=expand_universe)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return jsonable_encoder(result)


@router.get("/discovery/universe")
def get_discovery_universe(service: DiscoveryDep) -> dict[str, Any]:
    return jsonable_encoder(service.list_universe())


@router.post("/discovery/universe/scan")
def scan_discovery_universe(
    service: DiscoveryDep,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload or {}
    try:
        report = service.scan_universe(sync=bool(body.get("sync", True)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return jsonable_encoder(report.to_dict())

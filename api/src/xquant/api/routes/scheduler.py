from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from xquant.scheduler.domain import ExecutionStatus, ScheduleDefinition
from xquant.scheduler.runtime import SchedulerRuntime

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


def get_scheduler_runtime(request: Request) -> SchedulerRuntime:
    runtime = getattr(request.app.state, "scheduler_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="定时任务模块未启用")
    return runtime


SchedulerDep = Annotated[SchedulerRuntime, Depends(get_scheduler_runtime)]


def _engine_is_running(runtime: SchedulerRuntime) -> bool:
    return bool(getattr(runtime.engine, "running", False))


@router.get("/tasks")
async def list_tasks(runtime: SchedulerDep) -> dict[str, Any]:
    return {"items": await runtime.service.list_tasks()}


@router.get("/tasks/{name}")
async def get_task(name: str, runtime: SchedulerDep) -> Any:
    try:
        return await runtime.service.get_task(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


@router.post("/tasks/{name}/run")
async def run_task(
    name: str,
    runtime: SchedulerDep,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = payload or {}
    try:
        return await runtime.service.run_task(
            name,
            body.get("params"),
            priority=body.get("priority"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schedules")
async def list_schedules(
    runtime: SchedulerDep,
    enabled: bool | None = Query(default=None),
) -> dict[str, Any]:
    return {
        "items": [
            schedule.to_dict()
            for schedule in await runtime.service.list_schedules(enabled=enabled)
        ],
    }


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, runtime: SchedulerDep) -> Any:
    try:
        return (await runtime.service.get_schedule(schedule_id)).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="调度计划不存在") from exc


@router.post("/schedules")
async def create_schedule(
    runtime: SchedulerDep,
    payload: dict[str, Any],
) -> Any:
    data = dict(payload)
    data.setdefault("id", uuid4().hex)
    if not str(data.get("task_name") or "").strip():
        raise HTTPException(status_code=400, detail="task_name 不能为空")
    if not isinstance(data.get("trigger"), dict):
        raise HTTPException(status_code=400, detail="trigger 必须是对象")
    try:
        schedule = ScheduleDefinition.from_dict(data)
        saved = await runtime.service.save_schedule(schedule)
        if _engine_is_running(runtime):
            await runtime.engine.add_schedule(saved)
        return saved.to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    runtime: SchedulerDep,
    payload: dict[str, Any],
) -> Any:
    try:
        current = await runtime.service.get_schedule(schedule_id)
        data = current.to_dict()
        data.update(payload)
        data["id"] = schedule_id
        schedule = ScheduleDefinition.from_dict(data)
        saved = await runtime.service.save_schedule(schedule, recompute_next=True)
        if _engine_is_running(runtime):
            await runtime.engine.remove_schedule(schedule_id)
            await runtime.engine.add_schedule(saved)
        return saved.to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="调度计划不存在") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, runtime: SchedulerDep) -> dict[str, bool]:
    deleted = await runtime.service.delete_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="调度计划不存在")
    if _engine_is_running(runtime):
        await runtime.engine.remove_schedule(schedule_id)
    return {"deleted": True}


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(schedule_id: str, runtime: SchedulerDep) -> Any:
    try:
        schedule = await runtime.service.set_schedule_enabled(schedule_id, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="调度计划不存在") from exc
    if _engine_is_running(runtime):
        await runtime.engine.pause_schedule(schedule_id)
    return schedule.to_dict()


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(schedule_id: str, runtime: SchedulerDep) -> Any:
    try:
        schedule = await runtime.service.set_schedule_enabled(schedule_id, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="调度计划不存在") from exc
    if _engine_is_running(runtime):
        await runtime.engine.resume_schedule(schedule_id)
    return schedule.to_dict()


@router.get("/executions")
async def list_executions(
    runtime: SchedulerDep,
    status: Annotated[ExecutionStatus | None, Query()] = None,
    task_name: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    items = await runtime.service.execution_repository.list(
        status=status,
        task_name=task_name,
        limit=limit,
        offset=offset,
    )
    return {"items": items}


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, runtime: SchedulerDep) -> Any:
    execution = await runtime.service.execution_repository.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return execution


@router.post("/executions/{execution_id}/retry")
async def retry_execution(execution_id: str, runtime: SchedulerDep) -> Any:
    try:
        return await runtime.service.retry_execution(execution_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="执行记录不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str, runtime: SchedulerDep) -> Any:
    try:
        return await runtime.service.cancel_execution(execution_id)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.get("/workers")
async def list_workers(runtime: SchedulerDep) -> dict[str, Any]:
    heartbeat = runtime.heartbeat
    if heartbeat is None:
        return {"items": []}
    worker_ids = sorted(await heartbeat.live_worker_ids())
    return {"items": [{"worker_id": worker_id} for worker_id in worker_ids]}

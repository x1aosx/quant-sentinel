from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from xquant.ai.coordinator import MonitorManager
from xquant.alpha_lab.config import AlphaLabSettings
from xquant.alpha_lab.runtime import build_alpha_lab_runtime
from xquant.notifications.feishu import send_feishu_message
from xquant.registry import Database
from xquant.scheduler.application import TaskRegistry
from xquant.scheduler.runtime import build_scheduler_runtime
from xquant.storage import InfluxDBQueryError, StorageSettings
from xquant.system_config import SystemConfigStore
from xquant.task_bootstrap import register_all_tasks

from .intelligence_runtime import build_intelligence_runtime
from .routes import api_router


def create_app(
    db_path: Path | None = None,
    settings: StorageSettings | None = None,
    system_config_path: Path | None = None,
) -> FastAPI:
    settings = settings or StorageSettings.load()
    db = Database.from_settings(settings, db_path)
    config_path = system_config_path
    if config_path is None and db_path is not None:
        config_path = db_path.with_name("system-settings.json")
    system_config = SystemConfigStore(config_path)
    intelligence_runtime = build_intelligence_runtime(db, settings, system_config)
    alpha_lab_settings = AlphaLabSettings.from_env()
    if db_path is not None:
        artifact_root = db_path.with_name("alpha_lab")
        alpha_lab_settings = replace(
            alpha_lab_settings,
            artifact_root=artifact_root,
            snapshot_root=artifact_root / "snapshots",
        )
    alpha_lab_runtime = build_alpha_lab_runtime(db, alpha_lab_settings)

    def notify_monitor(record, _target, _state):
        feishu = system_config.settings.feishu
        return send_feishu_message(
            record,
            webhook_url=feishu.webhook_url,
            secret=feishu.secret,
            enabled=feishu.enabled,
            notify_on_order_only=feishu.notify_on_order_only,
            confidence_threshold=feishu.confidence_threshold,
            proxy_url=system_config.settings.provider.proxy_url,
            timeout_seconds=system_config.settings.provider.timeout_seconds,
        )

    monitor = MonitorManager(db, notification_callback=notify_monitor)
    scheduler_registry = TaskRegistry()
    if settings.scheduler.enabled:
        register_all_tasks(
            scheduler_registry,
            db,
            intelligence_service=intelligence_runtime.service,
            alpha_lab_runtime=alpha_lab_runtime,
        )
    scheduler_runtime = build_scheduler_runtime(
        db,
        settings,
        registry=scheduler_registry,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            if scheduler_runtime is not None:
                await scheduler_runtime.start(
                    start_engine=settings.scheduler.embedded,
                    start_worker=False,
                    start_recovery=False,
                )
            yield
        finally:
            if scheduler_runtime is not None:
                await scheduler_runtime.shutdown()
            if alpha_lab_runtime is not None:
                alpha_lab_runtime.shutdown()
            monitor.stop()
            close = getattr(db, "close", None)
            if callable(close):
                close()

    app = FastAPI(title="X-Quant API", version="0.3.0", lifespan=lifespan)

    @app.exception_handler(InfluxDBQueryError)
    async def _handle_influx_unavailable(
        _request: Request, exc: InfluxDBQueryError
    ) -> JSONResponse:
        # 行情存储故障属于上游依赖不可用，返回可读的 503 而不是裸 500。
        detail = "行情存储（InfluxDB）不可用"
        if exc.status_code is not None:
            detail += f"，上游返回 HTTP {exc.status_code}"
        if exc.detail:
            detail += f"：{exc.detail}"
        return JSONResponse(status_code=503, content={"detail": detail})

    app.state.db = db
    app.state.settings = settings
    app.state.system_config = system_config
    app.state.monitor = monitor
    app.state.intelligence_service = intelligence_runtime.api_service
    app.state.discovery_service = intelligence_runtime.discovery_service
    app.state.alpha_lab_runtime = alpha_lab_runtime
    app.state.alpha_lab_service = (
        alpha_lab_runtime.service if alpha_lab_runtime is not None else None
    )
    app.state.scheduler_runtime = scheduler_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    return app

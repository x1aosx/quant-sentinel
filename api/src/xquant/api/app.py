from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from xquant.ai.coordinator import MonitorManager
from xquant.notifications.feishu import send_feishu_message
from xquant.registry import Database
from xquant.storage import StorageSettings
from xquant.system_config import SystemConfigStore

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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        monitor.stop()
        close = getattr(db, "close", None)
        if callable(close):
            close()

    app = FastAPI(title="X-Quant API", version="0.3.0", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.system_config = system_config
    app.state.monitor = monitor
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    return app

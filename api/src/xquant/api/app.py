from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from xquant.registry import Database
from xquant.storage import StorageSettings

from .routes import api_router


def create_app(db_path: Path | None = None, settings: StorageSettings | None = None) -> FastAPI:
    settings = settings or StorageSettings.load()
    db = Database.from_settings(settings, db_path)
    app = FastAPI(title="X-Quant API", version="0.2.0")
    app.state.db = db
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    return app

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from xquant.domain.models import Bar
from xquant.execution.replay import run_replay
from xquant.marketdata.synthetic import generate_synthetic_bars
from xquant.registry.sqlite import Database


def create_app(db_path: Path | None = None) -> FastAPI:
    db_path = db_path or Path(os.getenv("XQUANT_DB_PATH", "data/xquant.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    app = FastAPI(title="X-Quant API", version="0.1.0")
    app.state.db = db
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "research",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "demo": True,
        }

    @app.get("/api/v1/dashboard")
    def dashboard() -> dict[str, Any]:
        return {
            "running_instances": 0,
            "data_status": "DEMO",
            "risk_status": "OK",
            "pending_plans": 0,
            "recent_experiments": db.list_experiments(),
            "watermark": "DEMO",
        }

    @app.get("/api/v1/strategies")
    def strategies() -> dict[str, Any]:
        items = db.list_strategies()
        if not items:
            items = [
                {
                    "id": "xq.srpa.breakout_retest.long",
                    "version": "0.1.0",
                    "title": "支撑阻力突破回测做多",
                    "research_status": "SPECIFIED",
                    "evidence_level": "E1",
                    "market": "CN_A_EQUITY",
                    "frequency": "1d",
                    "source_refs": ["R03", "R08", "R12", "R15"],
                    "tags": ["S-BR", "SR", "PA"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
            for item in items:
                db.upsert_strategy(item)
        return {"items": items}

    @app.get("/api/v1/strategies/")
    def strategies_slash() -> dict[str, Any]:
        return strategies()

    @app.get("/api/v1/strategies/{strategy_id}")
    def strategy_detail(strategy_id: str) -> dict[str, Any]:
        items = strategies()["items"]
        match = next((s for s in items if s["id"] == strategy_id), None)
        return match or {"error": "not_found"}

    @app.post("/api/v1/experiments")
    def create_experiment() -> dict[str, Any]:
        bars = generate_synthetic_bars("DEMO.EXAMPLE", n=180, seed=42)
        result = run_replay(bars, run_id=f"demo-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        exp = {
            "id": result.run_id,
            "strategy_id": "xq.srpa.breakout_retest.long",
            "status": "COMPLETED",
            "run_id": result.run_id,
            "summary": result.summary,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        db.insert_experiment(exp)
        return exp

    @app.get("/api/v1/experiments")
    def list_experiments() -> dict[str, Any]:
        return {"items": db.list_experiments()}

    @app.get("/api/v1/experiments/{experiment_id}")
    def experiment_detail(experiment_id: str) -> dict[str, Any]:
        bars = generate_synthetic_bars("DEMO.EXAMPLE", n=180, seed=42)
        result = run_replay(bars, run_id=experiment_id)
        return {
            "id": experiment_id,
            "status": "COMPLETED",
            "events": result.events,
            "plans": result.plans,
            "equity_curve": result.equity_curve,
            "summary": result.summary,
        }

    @app.get("/api/v1/instances")
    def list_instances() -> dict[str, Any]:
        return {"items": []}

    @app.get("/api/v1/plans")
    def list_plans() -> dict[str, Any]:
        return {"items": []}

    @app.get("/api/v1/positions")
    def list_positions() -> dict[str, Any]:
        return {"items": [], "cash": 100_000.0, "equity": 100_000.0}

    @app.get("/api/v1/risk")
    def list_risk() -> dict[str, Any]:
        return {
            "cash": 100_000.0,
            "equity": 100_000.0,
            "gross_exposure": 0.0,
            "gross_exposure_limit": 0.8,
            "sector_exposure": [],
            "limits": [
                {"name": "单笔计划风险", "current": 0.0, "limit": 0.0025, "unit": "equity", "status": "OK"},
                {"name": "总多头市值", "current": 0.0, "limit": 0.8, "unit": "equity", "status": "OK"},
                {"name": "持仓股票数", "current": 0, "limit": 8, "unit": "symbols", "status": "OK"},
            ],
            "flags": ["DEMO 合成数据，不可用于真实部署"],
        }

    @app.get("/api/v1/data")
    def data_status() -> dict[str, Any]:
        return {
            "items": [
                {
                    "source": "eastmoney",
                    "coverage": 0,
                    "last_updated": None,
                    "quality_flags": ["DEMO", "VINTAGE_UNVERIFIED"],
                    "revision": "demo-snapshot",
                    "snapshots": 0,
                },
                {
                    "source": "gate",
                    "coverage": 0,
                    "last_updated": None,
                    "quality_flags": ["DEMO", "UNAUTHORIZED"],
                    "revision": "none",
                    "snapshots": 0,
                },
            ]
        }

    @app.get("/api/v1/notifications")
    def notifications() -> dict[str, Any]:
        return {"items": []}

    @app.post("/api/v1/replay")
    def start_replay(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        instrument = str(payload.get("instrument_id") or "DEMO.EXAMPLE")
        n = int(payload.get("n") or 180)
        run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        bars = generate_synthetic_bars(instrument, n=n, seed=42)
        result = run_replay(bars, run_id=run_id)
        return {
            "run_id": result.run_id,
            "strategy_id": "xq.srpa.breakout_retest.long",
            "instrument_id": result.instrument_id,
            "snapshot_id": "demo-snapshot",
            "status": "SUCCEEDED",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "events": result.events,
            "trade_plans": result.plans,
            "equity_curve": result.equity_curve,
            "summary": result.summary,
        }

    @app.get("/api/v1/events")
    def list_events() -> dict[str, Any]:
        return {"items": []}

    return app

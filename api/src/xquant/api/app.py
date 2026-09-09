from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from xquant.analysis.price_action import analyze_price_action
from xquant.analysis.sr_levels import detect_support_resistance
from xquant.marketdata.synthetic import generate_synthetic_bars
from xquant.registry.sqlite import Database


def create_app(db_path: Path | None = None) -> FastAPI:
    db_path = db_path or Path(os.getenv("XQUANT_DB_PATH", "data/xquant.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    app = FastAPI(title="X-Quant API", version="0.2.0")
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
            "version": "0.2.0",
            "mode": "local_research",
            "dataset_count": len(db.list_datasets()),
        }

    @app.get("/api/v1/dashboard")
    def dashboard() -> dict[str, Any]:
        datasets = db.list_datasets()
        return {
            "status": "ok",
            "mode": "local_research",
            "dataset_count": len(datasets),
            "latest_dataset": datasets[0] if datasets else None,
        }

    @app.post("/api/v1/datasets")
    def create_dataset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        try:
            symbol = str(payload.get("symbol") or "").strip()
            timeframe = str(payload.get("timeframe") or "1d").strip() or "1d"
            if not symbol:
                raise HTTPException(status_code=400, detail="品种名称不能为空")
            bars = normalize_bars(payload.get("bars"))
            if len(bars) < 60:
                raise HTTPException(status_code=400, detail="数据不足：至少需要 60 根K线")
            dataset = db.insert_dataset(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bars": bars,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return dataset

    @app.post("/api/v1/datasets/sample")
    def create_sample_dataset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        timeframe = str(payload.get("timeframe") or "1d").strip() or "1d"
        model_bars = generate_synthetic_bars("DEMO.RESEARCH", n=180, seed=42)
        bars = normalize_bars(
            {
                "bars": [
                    {
                        "session_id": bar.session_id,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume_shares,
                    }
                    for bar in model_bars
                ]
            }
        )
        return db.insert_dataset(
            {
                "symbol": "DEMO.RESEARCH",
                "timeframe": timeframe,
                "bars": bars,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    @app.get("/api/v1/datasets")
    def list_datasets() -> dict[str, Any]:
        return {"items": db.list_datasets()}

    @app.get("/api/v1/datasets/{dataset_id}")
    def get_dataset(dataset_id: str) -> dict[str, Any]:
        return get_dataset_or_404(dataset_id)["summary"]

    @app.post("/api/v1/analysis/support-resistance")
    def support_resistance(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        dataset = get_dataset_or_404(str(payload.get("dataset_id") or ""))
        try:
            result = detect_support_resistance(
                dataset["bars"],
                symbol=dataset["summary"]["symbol"],
                timeframe=dataset["summary"]["timeframe"],
                lookback=_int_in(payload.get("lookback"), 60, 1000, 250),
                n_zones=_int_in(payload.get("n_zones"), 2, 8, 6),
                direction=str(payload.get("direction") or "both"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        risk_reward = result.setdefault("summary", {}).setdefault("risk_reward", {})
        risk_reward.setdefault("risk_reward_ratio", risk_reward.get("ratio"))
        price = float(result.get("current_price") or 0.0)
        atr = float(result.get("atr") or 0.0)
        if price and atr:
            risk_reward.setdefault(
                "potential_profit_pct",
                float(risk_reward.get("potential_reward_atr") or 0.0) * atr / price * 100.0,
            )
            risk_reward.setdefault(
                "potential_loss_pct",
                float(risk_reward.get("potential_risk_atr") or 0.0) * atr / price * 100.0,
            )
        result["candles"] = dataset["bars"][-150:]
        return result

    @app.post("/api/v1/analysis/price-action")
    def price_action(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        dataset = get_dataset_or_404(str(payload.get("dataset_id") or ""))
        try:
            result = analyze_price_action(
                dataset["bars"],
                symbol=dataset["summary"]["symbol"],
                timeframe=dataset["summary"]["timeframe"],
                lookback=_int_in(payload.get("lookback"), 60, 500, 120),
                risk_fraction=_number_in(payload.get("risk_fraction"), 0.0005, 0.1, 0.01),
                min_rr=_number_in(payload.get("min_rr"), 0.5, 10.0, 1.5),
                stance=str(payload.get("stance") or "conservative"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        atr = float(result.get("atr") or 0.0)
        supports = [float(v) for v in result.get("features", {}).get("supports", [])]
        resistances = [float(v) for v in result.get("features", {}).get("resistances", [])]
        levels = []
        for price in supports:
            half = max(atr * 0.25, price * 0.001)
            levels.append({"zone_type": "support", "center": price, "low": price - half, "high": price + half})
        for price in resistances:
            half = max(atr * 0.25, price * 0.001)
            levels.append({"zone_type": "resistance", "center": price, "low": price - half, "high": price + half})
        result["levels"] = levels
        result["candles"] = dataset["bars"][-150:]
        return result

    def get_dataset_or_404(dataset_id: str) -> dict[str, Any]:
        try:
            return db.get_dataset(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="数据集不存在") from exc

    return app


def _int_in(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _number_in(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_bars(raw_bars: Any) -> list[dict[str, Any]]:
    if isinstance(raw_bars, dict):
        raw_bars = raw_bars.get("bars")
    if not isinstance(raw_bars, list):
        raise TypeError("bars 必须是数组")
    if len(raw_bars) > 10_000:
        raise ValueError("单次导入不能超过 10,000 根K线")

    bars: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_bars):
        if not isinstance(raw, dict):
            raise TypeError(f"第 {index + 1} 根K线格式错误")
        try:
            open_ = float(raw["open"])
            high = float(raw["high"])
            low = float(raw["low"])
            close = float(raw["close"])
            volume = float(raw.get("volume", raw.get("tick_volume", 0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index + 1} 根K线的 OHLCV 无效") from exc
        if min(open_, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"第 {index + 1} 根K线价格必须大于 0，成交量不能小于 0")
        if low > min(open_, close) or high < max(open_, close):
            raise ValueError(f"第 {index + 1} 根K线高低价与开收盘价冲突")
        session = raw.get("session_id", raw.get("session", raw.get("time", raw.get("date"))))
        bars.append(
            {
                "session_id": str(session or f"S{index + 1:04d}"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return bars

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..scheduler.application import TaskRegistry
from .backtest import (
    AShareCostModel,
    AShareExecutionModel,
    BacktestEngine,
)
from .config import AlphaLabSettings
from .data import BarFrame, MarketDataPort, XQSMarketDataAdapter
from .domain import StrategyArtifact, StrategyStatus, TrainingRun, TrainingStatus
from .errors import AlphaLabError, InsufficientDataError
from .execution import (
    DisabledExecutionAdapter,
    DryRunExecutionAdapter,
    ExecutionService,
    RiskGate,
)
from .factor import FORMULA_VOCAB, SignalKernel
from .mining import MiningEngine
from .mining.walk_forward import WalkForwardPlan
from .realtime import (
    FileRealtimeWatchRepository,
    FileSignalStore,
    RealtimeAnalyzer,
    RealtimeWatch,
    watch_idempotency_key,
)
from .service import AlphaLabService
from .strategy import (
    FileStrategyRepository,
    artifact_from_alphamaster_json,
    artifact_to_alphamaster_json,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON object required: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _run_public(run: TrainingRun, **extra: Any) -> dict[str, Any]:
    payload = run.to_dict()
    payload.update(
        {
            "current_step": run.step,
            "metrics_json": extra.pop("metrics_json", {}),
            "config_json": extra.pop("config_json", {}),
            "metrics_history": extra.pop("metrics_history", []),
            "logs": extra.pop("logs", []),
        }
    )
    payload.update(extra)
    return payload


# 训练曲线与日志的保留上限：曲线保留每一步，日志只保留最近若干行。
_TRAINING_HISTORY_LIMIT = 2_000
_TRAINING_LOG_LIMIT = 400


def _log_entry(
    level: str,
    message: str,
    *,
    step: int | None = None,
) -> dict[str, Any]:
    return {
        "ts": _utc_now(),
        "level": level,
        "step": step,
        "message": message,
    }


def _append_log(
    logs: Sequence[Mapping[str, Any]],
    entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    combined = [dict(item) for item in logs]
    combined.append(dict(entry))
    return combined[-_TRAINING_LOG_LIMIT:]


class TrainingManager:
    """Durable background training orchestration around the mining engine."""

    def __init__(
        self,
        settings: AlphaLabSettings,
        market_data: MarketDataPort,
        strategies: FileStrategyRepository,
        *,
        max_workers: int = 2,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.strategies = strategies
        self.root = settings.artifact_root / "training"
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="alpha-lab-training",
        )
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[None]] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.glob("*/run.json"):
            try:
                payload = _read_json(path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if str(payload.get("status")) in {
                TrainingStatus.QUEUED.value,
                TrainingStatus.RUNNING.value,
            }:
                payload["status"] = TrainingStatus.FAILED.value
                payload["error"] = "process restarted before training completed"
                _write_json(path, payload)
            self._runs[str(payload["id"])] = payload

    def list_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._runs.values())
        values.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return values[: max(1, int(limit))]

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        dataset_id = str(
            payload.get("data_snapshot_id")
            or payload.get("dataset_id")
            or ""
        ).strip()
        timeframe = str(payload.get("timeframe") or "1d").strip().lower() or "1d"
        dataset_title = ""
        if dataset_id:
            metadata = self.market_data.get_dataset_metadata(dataset_id)
            symbol = str(metadata.get("symbol") or "").strip().upper()
            if not symbol:
                raise ValueError("dataset symbol is missing")
            timeframe = str(metadata.get("timeframe") or timeframe).strip().lower() or "1d"
            dataset_title = str(metadata.get("title") or "").strip()
            symbols = [symbol]
        else:
            raw_symbols = payload.get("symbols") or [payload.get("symbol")]
            if not isinstance(raw_symbols, Sequence) or isinstance(
                raw_symbols, (str, bytes)
            ):
                raise TypeError("symbols must be a list")
            symbols = [
                str(item).strip().upper() for item in raw_symbols if str(item).strip()
            ]
        if len(symbols) != 1:
            raise ValueError("current training implementation supports one symbol per run")
        frame = self.market_data.load_bars(
            symbols[0],
            timeframe,
            dataset_id=dataset_id or None,
            closed_only=True,
        )
        minimum_bars = int(
            payload.get("min_bars")
            or self.settings.mining.training_min_bars
        )
        self._validate_training_bars(frame, minimum_bars)
        total_steps = max(1, min(2_000, int(payload.get("total_steps") or 50)))
        batch_size = max(1, min(512, int(payload.get("batch_size") or 32)))
        seed = int(payload.get("seed", self.settings.mining.seed))
        run_id = uuid.uuid4().hex[:20]
        run_name = (
            str(payload.get("name") or "").strip()
            or dataset_title
            or f"{frame.symbol} {frame.timeframe}"
        )
        run = TrainingRun(
            id=run_id,
            dataset_id=dataset_id or str(frame.symbol),
            symbol=frame.symbol,
            timeframe=frame.timeframe,
        )
        public = _run_public(
            run,
            name=run_name,
            market=str(payload.get("market") or "CN-A"),
            dataset_title=dataset_title,
            total_steps=total_steps,
            seed=seed,
            config_json={
                "batch_size": batch_size,
                "total_steps": total_steps,
                "profile": str(payload.get("config_profile") or "alpha_master_compat"),
                "seed": seed,
                "device": str(payload.get("device") or "auto"),
                "symbol": frame.symbol,
                "timeframe": frame.timeframe,
                "bars": frame.n_bars,
            },
            metrics_history=[],
            logs=[
                _log_entry(
                    "info",
                    f"训练任务已创建：{frame.symbol} {frame.timeframe}，"
                    f"{frame.n_bars} 根已收盘 K 线，"
                    f"步数 {total_steps}，批量 {batch_size}，seed {seed}",
                )
            ],
        )
        self._save_run(public)
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[run_id] = cancel_event
            self._futures[run_id] = self._executor.submit(
                self._train,
                run_id,
                frame,
                total_steps,
                batch_size,
                seed,
                cancel_event,
            )
        return public

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id]

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                raise KeyError(run_id)
            status = str(current.get("status") or "")
            if status not in {TrainingStatus.QUEUED.value, TrainingStatus.RUNNING.value}:
                raise ValueError(f"training run cannot be cancelled from {status}")
            self._cancel_events[run_id].set()
            updated = {
                **current,
                "status": TrainingStatus.CANCELLED.value,
                "logs": _append_log(
                    current.get("logs") or [],
                    _log_entry("warn", "收到取消请求，训练将在当前步结束后停止"),
                ),
                "updated_at": _utc_now(),
            }
            self._runs[run_id] = updated
            self._save_run(updated)
            return updated

    def _validate_training_bars(self, frame: BarFrame, minimum_bars: int) -> None:
        """训练数据量校验：minimum_bars 快速失败 + walk-forward 可行性。

        可行性判断直接复用训练引擎的 WalkForwardPlan.adaptive，保证前置校验与
        实际训练对「多少 bar 才够」的判断完全一致。DEMO.RESEARCH 样本数据集豁免。
        """
        if frame.n_bars < minimum_bars and frame.symbol != "DEMO.RESEARCH":
            raise InsufficientDataError(
                f"insufficient training bars: {frame.n_bars}/{minimum_bars}"
            )
        if frame.symbol == "DEMO.RESEARCH":
            return
        try:
            WalkForwardPlan.adaptive(frame.n_bars, self.settings.mining)
        except ValueError as exc:
            raise InsufficientDataError(
                f"insufficient training bars for walk-forward: "
                f"n_bars={frame.n_bars} ({exc})"
            ) from exc

    def _train(
        self,
        run_id: str,
        frame: BarFrame,
        total_steps: int,
        batch_size: int,
        seed: int,
        cancel_event: threading.Event,
    ) -> None:
        current = self.get_run(run_id)
        run = TrainingRun(
            id=run_id,
            dataset_id=str(current.get("dataset_id") or ""),
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            status=TrainingStatus.RUNNING,
        )
        engine = MiningEngine(
            frame,
            run,
            settings=self.settings.mining,
            batch_size=batch_size,
            seed=seed,
        )
        metrics: dict[str, Any] = {}
        # 训练曲线与日志在内存中始终保留完整版本；run.json 只在里程碑步与结束时
        # 落盘，避免每一步都重写一份不断变大的 JSON。
        history: list[dict[str, Any]] = list(current.get("metrics_history") or [])
        logs: list[dict[str, Any]] = list(current.get("logs") or [])
        persist_every = max(1, total_steps // 100)
        log_every = max(1, total_steps // 20)

        def snapshot(
            training_run: TrainingRun,
            *,
            persist: bool = True,
            **extra: Any,
        ) -> None:
            self._save_run(
                _run_public(
                    training_run,
                    name=current.get("name"),
                    market=current.get("market"),
                    dataset_title=current.get("dataset_title"),
                    total_steps=total_steps,
                    seed=seed,
                    config_json=current.get("config_json", {}),
                    metrics_json=metrics,
                    metrics_history=history,
                    logs=logs,
                    **extra,
                ),
                persist=persist,
            )

        try:
            logs = _append_log(
                logs,
                _log_entry("info", "训练开始，正在初始化挖掘引擎"),
            )
            snapshot(run)
            for step in range(total_steps):
                if cancel_event.is_set():
                    break
                metrics = engine.train_step().to_dict()
                history.append({"ts": _utc_now(), **metrics})
                if len(history) > _TRAINING_HISTORY_LIMIT:
                    del history[: len(history) - _TRAINING_HISTORY_LIMIT]
                run = replace(
                    run,
                    status=TrainingStatus.RUNNING,
                    step=step + 1,
                    progress=(step + 1) / total_steps,
                    best_formula_tokens=engine.best_formula_tokens,
                    best_score=engine.best_score,
                    updated_at=_utc_now(),
                )
                finished = step + 1 == total_steps
                if finished or (step + 1) % log_every == 0:
                    logs = _append_log(
                        logs,
                        _log_entry(
                            "info",
                            f"进度 {step + 1}/{total_steps}："
                            f"reward={float(metrics.get('reward', 0.0)):.4f}，"
                            f"验证得分={float(metrics.get('validation_score', 0.0)):.4f}，"
                            f"最优得分={float(metrics.get('best_score', 0.0)):.4f}",
                            step=step + 1,
                        ),
                    )
                if float(metrics.get("invalid_rate", 0.0)) >= 0.5 and (
                    finished or (step + 1) % log_every == 0
                ):
                    logs = _append_log(
                        logs,
                        _log_entry(
                            "warn",
                            f"无效公式比例偏高：{float(metrics.get('invalid_rate', 0.0)):.2%}，"
                            "采样约束可能需要收紧",
                            step=step + 1,
                        ),
                    )
                snapshot(
                    run,
                    persist=finished or (step + 1) % persist_every == 0,
                )

            checkpoint = engine.checkpoint()
            checkpoint_path = self.root / run_id / "checkpoint.json"
            checkpoint.save(checkpoint_path)
            logs = _append_log(
                logs,
                _log_entry("info", f"检查点已保存：{checkpoint_path}"),
            )

            best_strategy_id: str | None = None
            if engine.best_formula_tokens:
                best_strategy_id = f"alpha-{run_id}"
                artifact = StrategyArtifact(
                    strategy_id=best_strategy_id,
                    version="1.0.0",
                    name=str(current.get("name") or best_strategy_id),
                    symbol=frame.symbol,
                    timeframe=frame.timeframe,
                    formula_tokens=engine.best_formula_tokens,
                    factor_schema_version=FORMULA_VOCAB.schema_version,
                    signal_kernel=SignalKernel.version,
                    min_exposure=self.settings.mining.min_exposure,
                    status=StrategyStatus.CANDIDATE,
                    data_snapshot_id=str(current.get("dataset_id") or ""),
                    training_run_id=run_id,
                    metadata={
                        "best_score": engine.best_score,
                        "metrics": metrics,
                        "lineage": {
                            "trained_from": run_id,
                            "dataset_id": current.get("dataset_id"),
                        },
                    },
                )
                self.strategies.save(artifact)
                logs = _append_log(
                    logs,
                    _log_entry(
                        "info",
                        f"已产出候选策略 {best_strategy_id}，"
                        f"最优得分 {float(engine.best_score or 0.0):.4f}",
                    ),
                )
            else:
                logs = _append_log(
                    logs,
                    _log_entry("warn", "训练结束但没有可用的最优公式，未产出策略"),
                )

            status = (
                TrainingStatus.CANCELLED
                if cancel_event.is_set()
                else TrainingStatus.SUCCEEDED
            )
            logs = _append_log(
                logs,
                _log_entry(
                    "warn" if status is TrainingStatus.CANCELLED else "info",
                    "训练已取消" if status is TrainingStatus.CANCELLED else "训练完成",
                ),
            )
            run = replace(
                run,
                status=status,
                progress=1.0 if status is TrainingStatus.SUCCEEDED else run.progress,
                best_formula_tokens=engine.best_formula_tokens,
                best_score=engine.best_score,
                checkpoint_uri=str(checkpoint_path),
                updated_at=_utc_now(),
            )
            snapshot(
                run,
                best_strategy_id=best_strategy_id,
                finished_at=_utc_now(),
            )
        except (AlphaLabError, ArithmeticError, IndexError, ValueError) as exc:
            failed = replace(
                run,
                status=TrainingStatus.FAILED,
                error=str(exc),
                updated_at=_utc_now(),
            )
            logs = _append_log(
                logs,
                _log_entry("error", f"训练失败：{exc}", step=run.step or None),
            )
            snapshot(failed, finished_at=_utc_now())
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
                self._futures.pop(run_id, None)

    def _save_run(self, payload: dict[str, Any], *, persist: bool = True) -> None:
        run_id = str(payload["id"])
        with self._lock:
            self._runs[run_id] = payload
        if persist:
            _write_json(self.root / run_id / "run.json", payload)


class BacktestManager:
    def __init__(
        self,
        settings: AlphaLabSettings,
        market_data: MarketDataPort,
        strategies: FileStrategyRepository,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.strategies = strategies
        self.root = settings.artifact_root / "backtests"

    def list_results(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        results = [
            _read_json(path)
            for path in sorted(
                self.root.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        ]
        return results[: max(1, int(limit))]

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        strategy_id = str(payload.get("strategy_id") or "").strip()
        if not strategy_id:
            raise ValueError("strategy_id is required")
        artifact = self.strategies.get(
            strategy_id,
            str(payload.get("strategy_version") or payload.get("version") or "") or None,
        )
        dataset_id = str(
            payload.get("data_snapshot_id")
            or payload.get("dataset_id")
            or artifact.data_snapshot_id
            or artifact.metadata.get("dataset_id")
            or ""
        ).strip()
        frame = self.market_data.load_bars(
            artifact.symbol,
            artifact.timeframe,
            dataset_id=dataset_id or None,
            closed_only=True,
        )
        initial_capital = float(payload.get("initial_capital") or 1_000_000.0)
        commission = float(payload.get("commission_pct") or 0.03) / 100.0
        slippage = float(payload.get("slippage_pct") or 0.02) / 100.0
        cost_model = AShareCostModel(
            commission_rate=commission,
            slippage_rate=slippage,
        )
        execution_model = AShareExecutionModel(
            allow_short=bool(payload.get("allow_short", False)),
            t_plus_one=bool(payload.get("t_plus_one", True)),
        )
        kernel = SignalKernel(
            replace(
                self.settings.mining,
                min_exposure=float(
                    payload.get("min_exposure") or artifact.min_exposure
                ),
            )
        )
        engine = BacktestEngine(
            formula_tokens=artifact.formula_tokens,
            factor_schema_version=artifact.factor_schema_version,
            cost_model=cost_model,
            execution_model=execution_model,
            signal_kernel=kernel,
            initial_equity=initial_capital,
        )
        report = engine.run(
            frame,
            strategy_id=artifact.strategy_id,
            strategy_version=artifact.version,
            dataset_id=dataset_id,
        )
        result = report.to_dict()
        result["name"] = str(payload.get("name") or f"{artifact.name} backtest")
        result["status"] = "SUCCEEDED"
        result["strategy_name"] = artifact.name
        result["symbols"] = [artifact.symbol]
        result["timeframe"] = artifact.timeframe
        result["config_json"] = dict(payload)
        _write_json(self.root / f"{report.run_id}.json", result)
        return result


class StrategyManager:
    def __init__(self, repository: FileStrategyRepository) -> None:
        self.repository = repository

    def list_artifacts(self) -> list[dict[str, Any]]:
        return [_artifact_public(item) for item in self.repository.list()]

    def get_artifact(
        self,
        strategy_id: str,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        return _artifact_public(self.repository.get(strategy_id, version))

    def import_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = payload.get("strategy") or payload.get("payload") or payload
        artifact = artifact_from_alphamaster_json(
            raw,
            strategy_id=payload.get("strategy_id"),
            version=str(payload.get("version") or "1.0.0"),
            name=payload.get("name"),
        )
        return _artifact_public(self.repository.save(artifact))


class RealtimeManager:
    def __init__(self, analyzer: RealtimeAnalyzer) -> None:
        self.analyzer = analyzer

    def list_watches(self) -> list[dict[str, Any]]:
        watches = self.analyzer.watch_repository.list()
        return [self._watch_public(watch) for watch in watches]

    def create_watch(self, payload: dict[str, Any]) -> dict[str, Any]:
        strategy_id = str(payload.get("strategy_id") or "").strip()
        if not strategy_id:
            raise ValueError("strategy_id is required")
        source = str(payload.get("source") or "local").strip() or "local"
        symbol = str(payload.get("symbol") or "").strip().upper()
        timeframe = str(payload.get("timeframe") or "1d").strip().lower() or "1d"
        version = str(payload.get("strategy_version") or payload.get("version") or "") or None
        artifact = self.analyzer.strategy_repository.get(strategy_id, version)
        # 监控标的必须来自已有策略：策略与训练时的品种/周期绑定，换品种或换周期
        # 需要重新训练或单独验证，否则信号语义与回测结果都对不上。
        if symbol and symbol != str(artifact.symbol).strip().upper():
            raise ValueError(
                f"策略 {artifact.strategy_id} 训练标的为 {artifact.symbol}，"
                f"不能用于监控 {symbol}；请选择已有该标的策略的监控目标"
            )
        if timeframe != str(artifact.timeframe).strip().lower():
            raise ValueError(
                f"策略 {artifact.strategy_id} 训练周期为 {artifact.timeframe}，"
                f"不能用于监控 {timeframe} 周期"
            )
        watch_id = watch_idempotency_key(
            source,
            symbol or artifact.symbol,
            timeframe or artifact.timeframe,
            artifact.strategy_id,
            artifact.version,
        )
        existing = self.analyzer.watch_repository.get(watch_id)
        if existing is not None:
            return self._watch_public(existing)
        watch = RealtimeWatch(
            id=watch_id,
            source=source,
            symbol=symbol or artifact.symbol,
            timeframe=timeframe or artifact.timeframe,
            strategy_id=artifact.strategy_id,
            strategy_version=artifact.version,
            enabled=bool(payload.get("enabled", True)),
        )
        self.analyzer.watch_repository.save(watch)
        return self._watch_public(watch)

    def delete_watch(self, watch_id: str) -> bool:
        delete = getattr(self.analyzer.watch_repository, "delete", None)
        if not callable(delete):
            raise TypeError("watch repository does not support deletion")
        return bool(delete(watch_id))

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        watch_id = str(payload.get("watch_id") or "").strip()
        if watch_id:
            watch = self.analyzer.watch_repository.get(watch_id)
            if watch is None:
                raise KeyError(watch_id)
            record = self.analyzer.analyze(
                watch.strategy_id,
                symbol=watch.symbol,
                timeframe=watch.timeframe,
                source=watch.source,
                version=watch.strategy_version,
                watch_id=watch.id,
            )
            return {
                "evaluated": 1,
                "generated": 0 if record.idempotent else 1,
                "signals": [self._signal_public(record)],
                "errors": [],
            }

        strategy_id = str(payload.get("strategy_id") or "").strip()
        if strategy_id:
            record = self.analyzer.analyze(
                strategy_id,
                symbol=str(payload.get("symbol") or "") or None,
                timeframe=str(payload.get("timeframe") or "") or None,
                source=str(payload.get("source") or "local"),
                version=str(payload.get("strategy_version") or "") or None,
            )
            return {
                "evaluated": 1,
                "generated": 0 if record.idempotent else 1,
                "signals": [self._signal_public(record)],
                "errors": [],
            }

        signals: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        watches = [
            item for item in self.analyzer.watch_repository.list() if item.enabled
        ]
        for watch in watches:
            try:
                record = self.analyzer.analyze(
                    watch.strategy_id,
                    symbol=watch.symbol,
                    timeframe=watch.timeframe,
                    source=watch.source,
                    version=watch.strategy_version,
                    watch_id=watch.id,
                )
                signals.append(self._signal_public(record))
            except (AlphaLabError, KeyError, TypeError, ValueError) as exc:
                errors.append({"watch_id": watch.id, "message": str(exc)})
        return {
            "evaluated": len(watches),
            "generated": sum(1 for item in signals if not item.get("idempotent")),
            "signals": signals,
            "errors": errors,
        }

    def list_signals(
        self,
        *,
        limit: int = 200,
        watch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        records = self.analyzer.signal_store.list(limit=None)
        if watch_id:
            watch = self.analyzer.watch_repository.get(watch_id)
            if watch is None:
                return []
            records = [
                item
                for item in records
                if item.strategy_id == watch.strategy_id
                and item.strategy_version == watch.strategy_version
                and item.symbol == watch.symbol
                and item.timeframe == watch.timeframe
            ]
        names: dict[tuple[str, str], str] = {}
        payloads: list[dict[str, Any]] = []
        for item in records[: max(1, int(limit))]:
            payload = item.to_dict()
            key = (item.strategy_id, item.strategy_version)
            if key not in names:
                names[key] = self._strategy_name(*key)
            payload["strategy_name"] = names[key]
            payloads.append(payload)
        return payloads

    def _signal_public(self, record: Any) -> dict[str, Any]:
        payload = record.to_dict()
        payload["strategy_name"] = self._strategy_name(
            str(record.strategy_id),
            str(record.strategy_version or ""),
        )
        return payload

    def _strategy_name(self, strategy_id: str, version: str | None = None) -> str:
        try:
            artifact = self.analyzer.strategy_repository.get(
                strategy_id,
                version or None,
            )
        except (KeyError, TypeError, ValueError):
            return ""
        return str(artifact.name or "")

    def _watch_public(self, watch: RealtimeWatch) -> dict[str, Any]:
        payload = watch.to_dict()
        payload["last_error"] = watch.error
        payload["strategy_name"] = self._strategy_name(
            watch.strategy_id,
            watch.strategy_version,
        )
        return payload


@dataclass
class AlphaLabRuntime:
    settings: AlphaLabSettings
    market_data: XQSMarketDataAdapter
    strategies_repository: FileStrategyRepository
    strategies: StrategyManager
    training: TrainingManager
    backtest: BacktestManager
    realtime: RealtimeManager
    execution: ExecutionService
    service: AlphaLabService

    def shutdown(self) -> None:
        self.training._executor.shutdown(wait=False, cancel_futures=True)


def build_alpha_lab_runtime(
    database: Any,
    settings: AlphaLabSettings | None = None,
    *,
    enabled: bool | None = None,
) -> AlphaLabRuntime | None:
    resolved = settings or AlphaLabSettings.from_env()
    if enabled is not None and not enabled:
        return None
    if not resolved.enabled:
        return None
    market_data = XQSMarketDataAdapter(database)
    strategies_repository = FileStrategyRepository(resolved)
    strategies = StrategyManager(strategies_repository)
    training = TrainingManager(
        resolved,
        market_data,
        strategies_repository,
    )
    backtest = BacktestManager(resolved, market_data, strategies_repository)
    realtime = RealtimeManager(
        RealtimeAnalyzer(
            strategy_repository=strategies_repository,
            watch_repository=FileRealtimeWatchRepository(resolved),
            signal_store=FileSignalStore(resolved),
            settings=resolved,
            market_data=market_data,
        )
    )
    execution_adapter = (
        DryRunExecutionAdapter()
        if resolved.execution_enabled
        else DisabledExecutionAdapter()
    )
    execution = ExecutionService(
        resolved,
        RiskGate(resolved),
        execution_adapter,
    )
    service = AlphaLabService(
        training=training,
        backtest=backtest,
        strategies=strategies,
        realtime=realtime,
    )
    return AlphaLabRuntime(
        settings=resolved,
        market_data=market_data,
        strategies_repository=strategies_repository,
        strategies=strategies,
        training=training,
        backtest=backtest,
        realtime=realtime,
        execution=execution,
        service=service,
    )


def register_alpha_tasks(
    registry: TaskRegistry,
    runtime: AlphaLabRuntime,
) -> None:
    from .tasks import register_alpha_tasks as register

    register(registry, runtime)


def _artifact_public(artifact: StrategyArtifact) -> dict[str, Any]:
    payload = artifact_to_alphamaster_json(artifact)
    payload["strategy_id"] = artifact.strategy_id
    payload["metrics_json"] = dict(artifact.metadata.get("metrics") or {})
    payload["training_range"] = artifact.metadata.get("training_range")
    payload["validation_ranges"] = artifact.metadata.get("validation_ranges", [])
    payload["holdout_range"] = artifact.metadata.get("holdout_range")
    payload["lineage"] = artifact.metadata.get("lineage", {})
    payload["robustness_json"] = artifact.metadata.get("robustness", {})
    return payload


__all__ = [
    "AlphaLabRuntime",
    "BacktestManager",
    "RealtimeManager",
    "StrategyManager",
    "TrainingManager",
    "build_alpha_lab_runtime",
    "register_alpha_tasks",
]

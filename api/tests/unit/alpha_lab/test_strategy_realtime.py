from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from xquant.alpha_lab.config import AlphaLabSettings, MiningSettings
from xquant.alpha_lab.data import BarFrame
from xquant.alpha_lab.domain import (
    Direction,
    SignalState,
    StrategyArtifact,
    StrategyStatus,
)
from xquant.alpha_lab.errors import SchemaCompatibilityError
from xquant.alpha_lab.execution import (
    AccountSnapshot,
    DryRunExecutionAdapter,
    ExecutionContext,
    ExecutionService,
    ExecutionStatus,
    KillSwitch,
    OrderIntent,
    OrderSide,
    RiskGate,
)
from xquant.alpha_lab.factor import FORMULA_VOCAB
from xquant.alpha_lab.realtime import (
    FileRealtimeWatchRepository,
    InMemoryEventPublisher,
    InMemoryRealtimeWatchRepository,
    InMemorySignalStore,
    RealtimeAnalyzer,
    watch_idempotency_key,
)
from xquant.alpha_lab.strategy import (
    FileStrategyRepository,
    StrategyVersionConflictError,
    ensure_artifact_hash,
)


def _settings(tmp_path, *, min_bars: int = 1, execution_enabled: bool = False):
    mining = MiningSettings(
        training_min_bars=1,
        realtime_min_bars=min_bars,
        min_formula_length=1,
        min_exposure=0.01,
    )
    return AlphaLabSettings(
        enabled=True,
        execution_enabled=execution_enabled,
        artifact_root=tmp_path / "alpha-lab",
        snapshot_root=tmp_path / "alpha-lab" / "snapshots",
        mining=mining,
    )


def _artifact(*, status: StrategyStatus = StrategyStatus.CANDIDATE) -> StrategyArtifact:
    return ensure_artifact_hash(
        StrategyArtifact(
            strategy_id="alpha.ret.mean",
            version="1.0.0",
            name="RET mean",
            symbol="TEST",
            timeframe="1d",
            formula_tokens=(0,),
            factor_schema_version=FORMULA_VOCAB.schema_version,
            min_exposure=0.01,
            status=status,
        )
    )


def _frame(
    closes: list[float],
    *,
    closed: list[bool] | None = None,
    times: list[float] | None = None,
) -> BarFrame:
    close = np.asarray(closes, dtype=np.float64)[None, :]
    open_ = np.concatenate([close[:, :1], close[:, :-1]], axis=1)
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    volume = np.full_like(close, 1_000.0)
    timestamp = np.asarray(
        times if times is not None else list(range(len(closes))),
        dtype=np.float64,
    )[None, :]
    is_closed = np.asarray(
        closed if closed is not None else [True] * len(closes),
        dtype=bool,
    )[None, :]
    return BarFrame(
        symbol="TEST",
        timeframe="1d",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        time=timestamp,
        is_closed=is_closed,
        source="test",
    )


def test_strategy_artifact_hash_immutable_versions_and_import_export(tmp_path) -> None:
    settings = _settings(tmp_path)
    repository = FileStrategyRepository(settings)
    artifact = _artifact()

    saved = repository.save(artifact)
    assert saved.content_hash == artifact.content_hash
    assert repository.get(artifact.strategy_id, artifact.version).content_hash == saved.content_hash

    exported = repository.export_alphamaster(artifact.strategy_id)
    assert exported["vocab_version"] == FORMULA_VOCAB.schema_version
    assert exported["formula"] == [0]
    assert exported["factor_schema_version"] == FORMULA_VOCAB.schema_version

    imported = FileStrategyRepository(settings).import_alphamaster(
        {
            "symbol": "TEST",
            "timeframe": "1d",
            "formula": [0],
            "vocab_version": FORMULA_VOCAB.schema_version,
            "best_score": 1.25,
        },
        strategy_id="imported",
        version="0.1.0",
    )
    assert imported.metadata["best_score"] == 1.25
    assert imported.status is StrategyStatus.CANDIDATE

    conflicting = replace(artifact, formula_tokens=(1,), content_hash="")
    conflicting = ensure_artifact_hash(conflicting)
    with pytest.raises(StrategyVersionConflictError):
        repository.save(conflicting)

    validated = repository.validate(artifact.strategy_id, artifact.version)
    assert validated.status is StrategyStatus.VALIDATED
    production = repository.promote(artifact.strategy_id, artifact.version)
    assert production.status is StrategyStatus.PRODUCTION
    assert production.content_hash == artifact.content_hash


def test_strategy_schema_mismatch_is_hard_failure(tmp_path) -> None:
    repository = FileStrategyRepository(_settings(tmp_path))
    incompatible = ensure_artifact_hash(
        replace(
            _artifact(),
            factor_schema_version="v-incompatible",
            content_hash="",
        )
    )
    with pytest.raises(SchemaCompatibilityError):
        repository.save(incompatible)


def test_realtime_uses_closed_bars_only_and_deduplicates_signals(tmp_path) -> None:
    settings = _settings(tmp_path)
    strategy_repository = FileStrategyRepository(settings)
    artifact = strategy_repository.save(_artifact())
    watches = InMemoryRealtimeWatchRepository()
    signals = InMemorySignalStore()
    events = InMemoryEventPublisher()
    analyzer = RealtimeAnalyzer(
        strategy_repository=strategy_repository,
        watch_repository=watches,
        signal_store=signals,
        event_publisher=events,
        settings=settings,
    )
    frame = _frame(
        [100.0] * 60 + [160.0, 200.0],
        closed=[True] * 61 + [False],
    )

    first = analyzer.analyze_frame(artifact, frame)
    assert first.state is SignalState.OK
    assert first.bar_close_ts == "60.0"
    assert first.direction is Direction.LONG

    duplicate = analyzer.analyze_frame(artifact, frame)
    assert duplicate.idempotent is True
    assert duplicate.signal_key == first.signal_key
    assert len(signals) == 1
    assert events.events == []

    next_frame = _frame([100.0] * 60 + [160.0, 10.0])
    watch = watches.get(
        watch_idempotency_key(
            "test",
            "TEST",
            "1d",
            artifact.strategy_id,
            artifact.version,
        )
    )
    flipped = analyzer.analyze_frame(artifact, next_frame, watch=watch)
    assert flipped.direction is Direction.SHORT
    assert flipped.signal_key != first.signal_key
    assert len(events.events) == 1
    assert events.events[0].event_type == "alpha.signal.changed"
    assert events.events[0].previous_direction is Direction.LONG
    assert events.events[0].direction is Direction.SHORT


def test_realtime_watch_persistence_and_stale_detection(tmp_path) -> None:
    settings = _settings(tmp_path)
    strategy_repository = FileStrategyRepository(settings)
    artifact = strategy_repository.save(_artifact())
    watches = FileRealtimeWatchRepository(settings)
    analyzer = RealtimeAnalyzer(
        strategy_repository=strategy_repository,
        watch_repository=watches,
        settings=settings,
        stale_after_seconds=10.0,
    )
    stale = analyzer.analyze_frame(
        artifact,
        _frame([100.0 + index for index in range(61)], times=list(range(61))),
        now=100.0,
    )
    assert stale.state is SignalState.ERROR
    assert "stale market data" in stale.message

    watch_id = watch_idempotency_key(
        "test",
        "TEST",
        "1d",
        artifact.strategy_id,
        artifact.version,
    )
    persisted = FileRealtimeWatchRepository(settings).get(watch_id)
    assert persisted is not None
    assert persisted.state is SignalState.ERROR
    assert "stale market data" in persisted.error


def test_execution_defaults_to_rejection_and_honors_kill_switch(tmp_path) -> None:
    disabled_settings = _settings(tmp_path)
    production = replace(_artifact(), status=StrategyStatus.PRODUCTION)
    order = OrderIntent(
        order_id="order-1",
        strategy_id=production.strategy_id,
        strategy_version=production.version,
        symbol=production.symbol,
        side=OrderSide.BUY,
        quantity=10.0,
        reference_price=100.0,
    )
    dry_run = DryRunExecutionAdapter()
    disabled_service = ExecutionService(
        settings=disabled_settings,
        risk_gate=RiskGate(disabled_settings),
        adapter=dry_run,
    )

    rejected = disabled_service.submit(order, production)
    assert rejected.accepted is False
    assert rejected.status is ExecutionStatus.REJECTED
    assert rejected.raw["risk_code"] == "execution_disabled"
    assert dry_run.orders == []

    enabled_settings = _settings(tmp_path, execution_enabled=True)
    kill_switch = KillSwitch()
    gate = RiskGate(enabled_settings, kill_switch=kill_switch)
    service = ExecutionService(
        settings=enabled_settings,
        risk_gate=gate,
        adapter=dry_run,
    )
    allowed = service.submit(
        order,
        production,
        account=AccountSnapshot(account_id="test", equity=1_000_000.0),
        context=ExecutionContext(market_open=True, user_permitted=True),
    )
    assert allowed.accepted is True
    assert allowed.status is ExecutionStatus.DRY_RUN
    assert len(dry_run.orders) == 1

    kill_switch.activate("test stop")
    stopped = service.submit(
        replace(order, order_id="order-2"),
        production,
        account=AccountSnapshot(account_id="test", equity=1_000_000.0),
        context=ExecutionContext(market_open=True, user_permitted=True),
    )
    assert stopped.accepted is False
    assert stopped.raw["risk_code"] == "kill_switch_active"

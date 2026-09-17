# AlphaLab Architecture

## Runtime Composition

`build_alpha_lab_runtime()` is the single composition root for the subsystem:

```text
FastAPI create_app()
  └─ AlphaLabRuntime
      ├─ XQSMarketDataAdapter
      ├─ FileStrategyRepository
      ├─ TrainingManager
      ├─ BacktestManager
      ├─ RealtimeManager
      ├─ ExecutionService
      └─ AlphaLabService
```

The API layer only maps HTTP requests to `AlphaLabService`. It does not contain
factor, training, backtest, or realtime formulas.

## Core Contracts

- `BarFrame` is the canonical `[N, T]` OHLCV container.
- `FeatureRegistry` computes the ordered 65-feature tensor `[N, F, T]`.
- `OperatorRegistry` owns all 62 postfix operators.
- `FactorRuntime` executes token programs and enforces factor schema checks.
- `SignalKernel` is the only implementation of direction, position, and
  strength semantics.
- `MiningEngine`, `BacktestEngine`, and `RealtimeAnalyzer` all consume the same
  feature tensor, factor runtime, and signal kernel.

XQS `Database` remains the market-data and application persistence facade.
AlphaLab does not create a second market center, queue, notification service,
or FastAPI application.

## API Surface

All routes are mounted under `/api/v1/alphalab`:

```text
GET    /overview
GET    /training/runs
POST   /training/runs
GET    /training/runs/{run_id}
POST   /training/runs/{run_id}/cancel
GET    /strategies
GET    /strategies/{strategy_id}
POST   /strategies/import
GET    /backtests
POST   /backtests
GET    /realtime/watches
POST   /realtime/watches
DELETE /realtime/watches/{watch_id}
POST   /realtime/evaluate
GET    /realtime/signals
```

## Scheduler

The application and scheduler CLI share `register_all_tasks()`:

```text
alpha.training.run
alpha.backtest.run
alpha.realtime.evaluate
```

This avoids the previous split where plans could be created in one process while
another process lacked the registered handler.

## Safety

- Factor schema mismatch is a hard failure before token execution.
- Realtime analysis drops forming bars and is idempotent per closed bar.
- Strategy versions and content hashes are immutable.
- A-share execution defaults to T+1 and blocks short selling.
- Execution defaults to the disabled adapter; dry-run requires
  `ALPHA_LAB_EXECUTION_ENABLED=true`.

## Frontend

The existing root layout contains four AlphaLab pages:

```text
/alphalab/training
/alphalab/strategies
/alphalab/backtest
/alphalab/realtime
```

No second layout, query client, icon system, or frontend application was added.

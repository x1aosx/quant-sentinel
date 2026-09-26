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

## 展示字段契约

前端三个页面直接消费以下由 `runtime.py` 组装的字段，改动时需同步前端
`web/src/types/alphalab/*` 与 `web/src/api/alphalab/*`：

- 训练任务（`/training/runs`、`/training/runs/{id}`）：`metrics_history`
  （每一步一行，字段与 `mining.TrainingMetrics` 一致）、`logs`
  （`{ts, level, step, message}`，最多保留最近 400 行）、`dataset_title`。
  曲线与日志在内存中保留完整版本，`run.json` 只在里程碑步与结束时落盘。
- 回测结果（`/backtests`）：除既有指标外，`metrics` 追加
  `sortino` / `calmar` / `win_rate` / `profit_loss_ratio` / `profit_factor` /
  `average_holding_bars` / `exposure_mean` / `exposure_max`；顶层追加与
  `equity_curve` 逐点对齐的 `time_axis`、`time_axis_kind`（`session` 表示真实
  交易日，`bar_index` 表示合成序号）和 `rolling_sharpe`；`trade_log` 每笔追加
  `side` / `return_pct` / `entry_session` / `exit_session`。
- 实时监控（`/realtime/*`）：`create_watch` 强制监控标的与周期必须与所选策略
  训练时的品种、周期一致（否则 400）；watch 与 signal 均回传 `strategy_name`。

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

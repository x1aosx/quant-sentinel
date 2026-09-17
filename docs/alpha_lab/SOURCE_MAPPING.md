# AlphaMaster Source Mapping

This table maps upstream capabilities to X-Quant ownership boundaries. It is a
behavior map, not a source-copy map.

| AlphaMaster capability | X-Quant target |
| --- | --- |
| `model_core/features.py` | `xquant.alpha_lab.factor.FeatureRegistry` |
| `model_core/ops.py` | `xquant.alpha_lab.factor.OperatorRegistry` |
| `model_core/vocab.py` | `xquant.alpha_lab.factor.vocabulary` |
| `model_core/vm.py` | `xquant.alpha_lab.factor.stack_vm.FactorRuntime` |
| `strategy_manager/signal.py` | `xquant.alpha_lab.factor.SignalKernel` |
| `strategy_manager/live_signal.py` | `xquant.alpha_lab.realtime` |
| `model_core/alphagpt.py` | `xquant.alpha_lab.mining` policy model |
| `model_core/engine.py` | `xquant.alpha_lab.mining` training engine, sampler, reward, walk-forward, checkpoint |
| `model_core/backtest.py` | `xquant.alpha_lab.backtest` engine, execution model, costs, metrics |
| deep/audit scripts | `xquant.alpha_lab.backtest.robustness` |
| `strategies/*.json` | `xquant.alpha_lab.strategy` artifacts and repository |
| `data_pipeline/*` | `xquant.alpha_lab.data.MarketDataPort` plus existing XQS market data |
| `web/data_sources/*` | existing XQS provider adapters behind `MarketDataPort` |
| `web/training_manager.py` | AlphaLab application jobs and XQS Scheduler |
| `web/backtest_manager.py` | AlphaLab backtest service and XQS Scheduler |
| `web/realtime_manager.py` | AlphaLab realtime analyzer and XQS Scheduler |
| `web/feishu_notify.py` | existing `xquant.notifications` boundary |
| `execution/*` | feature-gated AlphaLab execution ports |
| `web/app.py` | XQS API routes under `/api/v1/alphalab` |
| `main.py` | no direct equivalent; entry points become application services |
| `train_file.py` | training runner or scheduler task |
| `run_backtest.py` | backtest service or CLI facade |

## Vocabulary

The ordered feature and operator registry is the migration boundary. The
independent runtime uses a distinct schema version until parity fixtures prove
semantic equivalence. The active registry is:

```text
feature token range: [0, 64]
operator token range: [65, 126]
feature count: 65
operator count: 62
total token count: 127
```

The concrete upstream names and ordering are represented by the X-Quant
registry. Changing a name or its order produces a different vocabulary version.
Production artifacts with a different version must fail before consuming any
token.

Current status is intentionally "same token vocabulary, independent semantics,
no upstream artifact compatibility". Importing an upstream `v9217a2c0d91a`
artifact must hard fail rather than silently execute different formulas.

## Data Boundary

AlphaLab consumes the existing XQS dataset facade through
`XQSMarketDataAdapter`. Factor and mining code must not import MT5, TradingView,
Eastmoney, Tencent, or other vendor clients directly.

## Persistence Boundary

AlphaLab uses the existing PostgreSQL, Redis, and InfluxDB services in deployed
environments. Local development may use the existing legacy SQLite facade.
Strategy artifacts and checkpoints are versioned data, not process-local files
treated as permanent production state.

## License Boundary

The upstream repository is AGPL-3.0. This mapping records compatibility work but
does not grant permission to copy source. Direct source reuse remains a separate
decision requiring license review.

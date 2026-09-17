# AlphaMaster Migration Report

## Upstream

- Repository: `https://github.com/rosemarycox5334-debug/AlphaMaster`
- Pinned commit: `4a0e851d1c865ec14143f8cd102070bf2c76ee83`
- License: AGPL-3.0
- Migration route: independent reimplementation

No upstream source, checkpoint, strategy file, or model weight was copied.

## Delivered

- Canonical bar and snapshot contracts
- 65-feature registry with deterministic ordering
- 62-operator registry and postfix StackVM
- Hard-fail factor schema compatibility
- Unified `tanh` position and LONG/SHORT/FLAT signal kernel
- Constrained formula sampler with valid stack generation
- Numpy policy-gradient training engine with entropy protection
- Reward scoring with IC, return, volatility, drawdown, turnover, cost, and
  correlation/repetition penalties
- Walk-forward folds with purge gap and final holdout
- Elite and factor pools
- Data-only checkpoint save/load and resume
- Immutable strategy artifact repository and AlphaMaster JSON import/export
- A-share cost model, T+1 execution timing, short-sale guard, metrics, equity,
  drawdown, trade log, multi-symbol support, and robustness audit
- Closed-bar realtime analysis, idempotent bars/signals, persistent watches,
  stale-data detection, and direction-flip events
- Disabled and dry-run execution adapters behind a risk gate and kill switch
- Unified application runtime, API routes, scheduler tasks, and four frontend
  pages

## Verification

- Backend full suite: `266 passed`
- AlphaLab API smoke test: training -> strategy -> backtest -> watch -> signal
- Frontend TypeScript and Vite production build: passed
- Browser smoke test: four desktop routes passed
- Mobile overflow check at `390x844`: passed

## Compatibility Status

The token names, ordering, feature count, operator count, and token offsets match
the pinned upstream vocabulary. The current implementation intentionally uses a
distinct runtime schema version, `xqs-v...`, because several independent formula
implementations have not yet been proven element-wise equivalent to upstream.

Upstream artifacts with version `v9217a2c0d91a` must therefore fail schema
validation instead of silently executing a different implementation.

## Remaining Work

- Generate the pinned upstream golden fixtures listed in
  `UPSTREAM_BASELINE.md`.
- Run feature, operator, VM, reward, fold, checkpoint, backtest, and realtime
  parity comparisons.
- Either align implementation semantics and hashes with upstream, or publish a
  migration tool that explicitly maps old artifacts to AlphaLab semantics.
- Replace file-backed production registries with PostgreSQL-backed repositories
  when moving beyond local research deployment.
- Add MT5/TradingView/OKX/Tongdaxin adapters behind `MarketDataPort` only when
  those providers are actually required by the deployment.

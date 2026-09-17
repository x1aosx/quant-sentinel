# AlphaMaster Upstream Baseline

## Frozen Source

- Repository: `https://github.com/rosemarycox5334-debug/AlphaMaster`
- Commit: `4a0e851d1c865ec14143f8cd102070bf2c76ee83`
- Upstream commit date: `2026-09-03`
- License: GNU Affero General Public License v3.0

The commit is pinned for audit and behavioral comparison only. The X-Quant
implementation does not vendor or copy AlphaMaster source files, checkpoints, or
pretrained artifacts.

## Migration Route

The project uses an independent reimplementation route:

1. Treat the pinned repository as a behavioral reference.
2. Freeze observable contracts, parameters, and edge cases in X-Quant tests.
3. Implement the behavior with X-Quant domain boundaries and existing
   infrastructure.
4. Do not preserve AlphaMaster's standalone FastAPI application, arbitrary file
   path API, filesystem-only strategy state, or permanent realtime thread.

This keeps AGPL-licensed implementation details outside the X-Quant codebase.
Any future direct source reuse requires a separate license decision and review.

## Preserved Behavioral Contracts

- Factor formulas are token programs, not Python expressions.
- The upstream active vocabulary contains 65 features and 62 operators.
- Feature tokens occupy `[0, 64]`; operator tokens occupy `[65, 126]`.
- X-Quant preserves the names and ordering as the migration vocabulary, but the
  independent runtime intentionally derives a distinct schema version from
  `xqs-alpha-runtime-v1` plus the token list.
- The upstream token-only version is `v9217a2c0d91a`. It must not be treated as
  compatible with the independent runtime until golden parity fixtures pass and
  the semantic implementation hash is aligned.
- Product behavior is intentionally independent of the upstream source:
  - `4.0-registry` vocabulary schema tag
  - deterministic vocabulary version derived from the ordered token names
- `StackVM` executes `[N, F, T]` feature tensors to `[N, T]`
  - `tanh` maps factor values to continuous positions
  - minimum exposure is `0.05`
  - `LONG`, `SHORT`, and `FLAT` thresholds use `+/- min_exposure`
  - realtime uses closed bars only
  - training and backtest use the same signal semantics as realtime
- Default realtime warm-up requirement is 800 bars.
- Training data warm-up requires 3,000 bars in the compatibility profile.
- Default minimum training bars are 3,000.
- Default formula length limit is 8 in the upstream training profile.
- Walk-forward purge gap default is 20 bars.
- Upstream correlation penalty threshold is 0.85 with penalty 0.8.
- Upstream IC gate threshold is 0.01 and gate multiplier is 1.15.
- Upstream IC negative multiplier is 0.75.

## Not Adopted

- AlphaMaster process-global training state
- AlphaMaster strategy JSON files as the only registry
- direct strategy file paths accepted by API requests
- provider imports inside factor, mining, or backtest core modules
- a second scheduler or notification subsystem
- real-money order execution enabled by default

## Reproduction Inputs

The migration reference audit should retain:

- pinned commit SHA
- upstream file inventory
- feature and operator order
- golden input bars and formulas
- expected features, factors, positions, and backtest output
- Python and dependency versions used to generate fixtures

The first parity fixtures must be generated from the pinned commit and stored
under `api/tests/fixtures/alpha_master/` when upstream execution is available.

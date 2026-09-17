import type { AlphaLabOverview } from '../../types/alphalab/common';
import type {
  StrategyArtifact,
  StrategyImportRequest,
} from '../../types/alphalab/strategy';
import {
  alphalabList,
  alphalabRequest,
  finiteNumber,
  optionalString,
  recordValue,
  stringArray,
} from './request';

function normalizeStrategy(value: unknown): StrategyArtifact {
  const raw = recordValue(value);
  const metadata = recordValue(raw.metadata);
  const metrics = {
    ...recordValue(metadata.metrics_json),
    ...recordValue(raw.metrics_json),
  };
  const formulaTokens = stringArray(raw.formula_tokens ?? raw.formula)
    .map(Number)
    .filter(Number.isFinite);
  return {
    ...raw,
    id: String(raw.id ?? raw.strategy_id ?? ''),
    name: String(raw.name ?? raw.strategy_id ?? ''),
    version: String(raw.version ?? ''),
    status: String(raw.status ?? 'CANDIDATE').toUpperCase(),
    symbols:
      stringArray(raw.symbols).length > 0
        ? stringArray(raw.symbols)
        : optionalString(raw.symbol)
          ? [String(raw.symbol)]
          : [],
    symbol_scope: optionalString(raw.symbol_scope) ?? optionalString(raw.symbol),
    formula_tokens: formulaTokens,
    formula_expression:
      optionalString(raw.formula_expression) ??
      optionalString(raw.formula_decoded),
    signal_kernel_version:
      optionalString(raw.signal_kernel_version) ??
      optionalString(raw.signal_kernel),
    train_score:
      finiteNumber(raw.train_score) ??
      finiteNumber(metrics.train_score) ??
      finiteNumber(metadata.best_score),
    validation_score:
      finiteNumber(raw.validation_score) ??
      finiteNumber(metrics.validation_score),
    holdout_score:
      finiteNumber(raw.holdout_score) ?? finiteNumber(metrics.holdout_score),
    backtest_score:
      finiteNumber(raw.backtest_score) ?? finiteNumber(metrics.backtest_score),
    metrics_json: {
      ...metrics,
      train_score:
        finiteNumber(raw.train_score) ??
        finiteNumber(metrics.train_score) ??
        finiteNumber(metadata.best_score),
      validation_score:
        finiteNumber(raw.validation_score) ??
        finiteNumber(metrics.validation_score),
      holdout_score:
        finiteNumber(raw.holdout_score) ?? finiteNumber(metrics.holdout_score),
      backtest_score:
        finiteNumber(raw.backtest_score) ?? finiteNumber(metrics.backtest_score),
    },
    robustness_json:
      recordValue(raw.robustness_json ?? raw.robustness ?? metadata.robustness),
    lineage: raw.lineage ?? metadata.lineage,
    realtime_references: Array.isArray(raw.realtime_references)
      ? (raw.realtime_references as Array<Record<string, unknown>>)
      : Array.isArray(metadata.realtime_references)
        ? (metadata.realtime_references as Array<Record<string, unknown>>)
        : [],
  } as StrategyArtifact;
}

export const strategyApi = {
  overview: () => alphalabRequest<AlphaLabOverview>('/alphalab/overview'),
  list: () =>
    alphalabRequest<unknown>('/alphalab/strategies')
      .then(alphalabList<unknown>)
      .then((items) => items.map(normalizeStrategy)),
  get: (id: string) =>
    alphalabRequest<unknown>(`/alphalab/strategies/${encodeURIComponent(id)}`)
      .then(normalizeStrategy),
  import: (payload: StrategyImportRequest) =>
    alphalabRequest<unknown>('/alphalab/strategies/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    }).then(normalizeStrategy),
};

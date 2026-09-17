import type { AlphaLabOverview } from '../../types/alphalab/common';
import type {
  TrainingRun,
  TrainingRunCreateRequest,
} from '../../types/alphalab/training';
import {
  alphalabList,
  alphalabRequest,
  finiteNumber,
  optionalString,
  recordValue,
  stringArray,
} from './request';

function normalizeRun(value: unknown): TrainingRun {
  const raw = recordValue(value);
  const symbols =
    stringArray(raw.symbols).length > 0
      ? stringArray(raw.symbols)
      : optionalString(raw.symbol)
        ? [String(raw.symbol)]
        : [];
  const bestTokens = stringArray(raw.best_formula_tokens);
  const metadata = recordValue(raw.metadata);
  return {
    ...raw,
    id: String(raw.id ?? raw.run_id ?? ''),
    symbols,
    symbol: optionalString(raw.symbol),
    timeframe: String(raw.timeframe ?? ''),
    dataset_id: optionalString(raw.dataset_id),
    data_snapshot_id:
      optionalString(raw.data_snapshot_id) ?? optionalString(raw.dataset_id),
    status: String(raw.status ?? 'PENDING'),
    progress: finiteNumber(raw.progress),
    step: finiteNumber(raw.step),
    current_step:
      finiteNumber(raw.current_step) ?? finiteNumber(raw.step),
    total_steps: finiteNumber(raw.total_steps),
    best_formula_tokens: bestTokens.map(Number).filter(Number.isFinite),
    best_formula:
      optionalString(raw.best_formula) ??
      (bestTokens.length ? `tokens: ${bestTokens.join(', ')}` : undefined),
    best_score:
      finiteNumber(raw.best_score) ?? finiteNumber(metadata.best_score),
    metrics_json: recordValue(raw.metrics_json ?? raw.metrics),
    error_message:
      optionalString(raw.error_message) ?? optionalString(raw.error),
    error: optionalString(raw.error),
    updated_at: optionalString(raw.updated_at),
  } as TrainingRun;
}

export const trainingApi = {
  overview: () => alphalabRequest<AlphaLabOverview>('/alphalab/overview'),
  list: () =>
    alphalabRequest<unknown>('/alphalab/training/runs')
      .then(alphalabList<unknown>)
      .then((items) => items.map(normalizeRun)),
  get: (id: string) =>
    alphalabRequest<unknown>(`/alphalab/training/runs/${encodeURIComponent(id)}`)
      .then(normalizeRun),
  create: (payload: TrainingRunCreateRequest) =>
    alphalabRequest<unknown>('/alphalab/training/runs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }).then(normalizeRun),
  cancel: (id: string) =>
    alphalabRequest<unknown>(
      `/alphalab/training/runs/${encodeURIComponent(id)}/cancel`,
      { method: 'POST' },
    ).then(normalizeRun),
};

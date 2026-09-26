import type { AlphaLabOverview } from '../../types/alphalab/common';
import type {
  TrainingLogEntry,
  TrainingMetricsPoint,
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

function normalizeMetricsPoint(value: unknown): TrainingMetricsPoint | null {
  const raw = recordValue(value);
  const step = finiteNumber(raw.step);
  if (step === undefined) return null;
  return { ...raw, step, ts: optionalString(raw.ts) };
}

function normalizeMetricsHistory(value: unknown): TrainingMetricsPoint[] {
  return (Array.isArray(value) ? value : []).flatMap((item) => {
    const point = normalizeMetricsPoint(item);
    return point ? [point] : [];
  });
}

function normalizeLogs(value: unknown): TrainingLogEntry[] {
  return (Array.isArray(value) ? value : []).flatMap((item) => {
    const raw = recordValue(item);
    const message = optionalString(raw.message);
    if (!message) return [];
    return [
      {
        ts: String(raw.ts ?? raw.timestamp ?? ''),
        level: String(raw.level ?? 'info').toLowerCase(),
        step: finiteNumber(raw.step) ?? null,
        message,
      },
    ];
  });
}

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
    dataset_title: optionalString(raw.dataset_title),
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
    metrics_history: normalizeMetricsHistory(raw.metrics_history),
    logs: normalizeLogs(raw.logs),
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

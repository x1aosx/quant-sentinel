import type { AlphaLabOverview } from '../../types/alphalab/common';
import type {
  RealtimeEvaluateRequest,
  RealtimeEvaluateResponse,
  RealtimeSignal,
  RealtimeWatch,
  RealtimeWatchCreateRequest,
} from '../../types/alphalab/realtime';
import {
  alphalabList,
  alphalabRequest,
  finiteNumber,
  optionalString,
  recordValue,
  withQuery,
} from './request';

function normalizeWatch(value: unknown): RealtimeWatch {
  const raw = recordValue(value);
  return {
    ...raw,
    id: String(raw.id ?? ''),
    strategy_id: String(raw.strategy_id ?? ''),
    strategy_version: optionalString(raw.strategy_version),
    source: String(raw.source ?? ''),
    symbol: String(raw.symbol ?? ''),
    timeframe: String(raw.timeframe ?? ''),
    enabled: Boolean(raw.enabled ?? true),
    state: String(raw.state ?? 'pending'),
    last_factor: finiteNumber(raw.last_factor),
    last_position: finiteNumber(raw.last_position),
    last_strength: finiteNumber(raw.last_strength),
    last_error: optionalString(raw.last_error),
    error: optionalString(raw.error),
  } as RealtimeWatch;
}

function normalizeSignal(value: unknown): RealtimeSignal {
  const raw = recordValue(value);
  return {
    ...raw,
    id: String(raw.id ?? raw.signal_key ?? ''),
    strategy_id: String(raw.strategy_id ?? ''),
    strategy_version: optionalString(raw.strategy_version),
    symbol: String(raw.symbol ?? ''),
    timeframe: String(raw.timeframe ?? ''),
    bar_close_ts: String(raw.bar_close_ts ?? ''),
    direction: String(raw.direction ?? 'FLAT'),
    position: finiteNumber(raw.position) ?? 0,
    strength: finiteNumber(raw.strength) ?? 0,
    factor_value: finiteNumber(raw.factor_value) ?? 0,
    created_at:
      optionalString(raw.created_at) ?? optionalString(raw.generated_at),
  } as RealtimeSignal;
}

export const realtimeApi = {
  overview: () => alphalabRequest<AlphaLabOverview>('/alphalab/overview'),
  listWatches: () =>
    alphalabRequest<unknown>('/alphalab/realtime/watches').then(
      (payload) => alphalabList<unknown>(payload).map(normalizeWatch),
    ),
  createWatch: (payload: RealtimeWatchCreateRequest) =>
    alphalabRequest<unknown>('/alphalab/realtime/watches', {
      method: 'POST',
      body: JSON.stringify(payload),
    }).then(normalizeWatch),
  deleteWatch: (id: string) =>
    alphalabRequest<{ ok?: boolean }>(
      `/alphalab/realtime/watches/${encodeURIComponent(id)}`,
      { method: 'DELETE' },
    ),
  evaluate: (payload: RealtimeEvaluateRequest = {}) =>
    alphalabRequest<RealtimeEvaluateResponse>('/alphalab/realtime/evaluate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listSignals: (params: { watch_id?: string; limit?: number } = {}) =>
    alphalabRequest<unknown>(
      withQuery('/alphalab/realtime/signals', {
        watch_id: params.watch_id,
        limit: params.limit ?? 100,
      }),
    ).then((payload) => alphalabList<unknown>(payload).map(normalizeSignal)),
};

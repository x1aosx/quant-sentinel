import type {
  DatasetBar,
  DatasetSummary,
  HealthSummary,
  PaAnalysisResult,
  SrAnalysisResult,
} from '../types';

const API_BASE = '/api/v1';

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = payload as { detail?: string; message?: string } | null;
    throw new Error(error?.detail ?? error?.message ?? `请求失败（${response.status}）`);
  }
  return payload as T;
}

export interface DatasetUpsertPayload {
  symbol: string;
  timeframe: string;
  bars: DatasetBar[];
}

export const api = {
  getHealth: () => apiRequest<HealthSummary>('/health'),
  getDashboard: () => apiRequest<{
    status: string;
    dataset_count: number;
    latest_dataset?: DatasetSummary | null;
    mode: string;
  }>('/dashboard'),
  listDatasets: () => apiRequest<{ items: DatasetSummary[] }>('/datasets'),
  uploadDataset: (payload: DatasetUpsertPayload) =>
    apiRequest<DatasetSummary>('/datasets', {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    }),
  generateSampleDataset: (timeframe = '1d') =>
    apiRequest<DatasetSummary>('/datasets/sample', {
      method: 'POST',
      body: JSON.stringify({ timeframe }),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    }),
  analyzeSupportResistance: (payload: {
    dataset_id: string;
    lookback?: number;
    n_zones?: number;
    direction?: 'both' | 'long' | 'short';
  }) =>
    apiRequest<SrAnalysisResult>('/analysis/support-resistance', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  analyzePriceAction: (payload: {
    dataset_id: string;
    lookback?: number;
    risk_fraction?: number;
    min_rr?: number;
    stance?: 'conservative' | 'balanced' | 'aggressive';
  }) =>
    apiRequest<PaAnalysisResult>('/analysis/price-action', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};

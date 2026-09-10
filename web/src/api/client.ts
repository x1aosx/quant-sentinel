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

async function requestCsv(path: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'text/csv' },
  });
  if (response.ok) {
    return response.blob();
  }

  let detail = '';
  try {
    const body = (await response.json()) as { detail?: unknown; message?: unknown };
    if (typeof body.detail === 'string') detail = body.detail;
    else if (typeof body.message === 'string') detail = body.message;
  } catch {
    // Response bodies for gateway errors are often plain HTML.
  }

  const statusMessages: Record<number, string> = {
    404: '行情下载接口不可用（HTTP 404）',
    502: '行情服务网关错误（HTTP 502），请稍后重试',
    503: '行情服务暂时不可用（HTTP 503），请稍后重试',
  };
  throw new Error(statusMessages[response.status] ?? detail ?? `行情下载失败（HTTP ${response.status}）`);
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
  downloadQuoteCsv: (payload: { symbol: string; timeframe: string; count: number }) => {
    const params = new URLSearchParams({
      symbol: payload.symbol,
      timeframe: payload.timeframe,
      count: String(payload.count),
    });
    return requestCsv(`/marketdata/quotes/download?${params.toString()}`);
  },
};

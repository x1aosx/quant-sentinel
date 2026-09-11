import type {
  AIAnalysisRecord,
  DatasetSummary,
  HealthSummary,
  PaAnalysisResult,
  SrAnalysisResult,
  SyncDatasetResponse,
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

export const api = {
  getHealth: () => apiRequest<HealthSummary>('/health'),
  getDashboard: () => apiRequest<{
    status: string;
    dataset_count: number;
    latest_dataset?: DatasetSummary | null;
    mode: string;
  }>('/dashboard'),
  listDatasets: () => apiRequest<{ items: DatasetSummary[] }>('/datasets'),
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
  syncRemoteDataset: (payload: {
    source: 'yfinance' | 'akshare';
    symbol: string;
    timeframe: string;
    lookback: number;
    adjust: 'qfq' | 'hfq' | 'none';
  }) =>
    apiRequest<SyncDatasetResponse>('/datasets/remote/sync', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importRemoteDataset: async (payload: {
    source: 'yfinance' | 'akshare';
    symbol: string;
    timeframe: string;
    lookback?: number;
    adjust?: 'qfq' | 'hfq' | 'none';
  }) => {
    const response = await api.syncRemoteDataset({
      source: payload.source,
      symbol: payload.symbol,
      timeframe: payload.timeframe,
      lookback: payload.lookback ?? 500,
      adjust: payload.adjust ?? 'qfq',
    });
    return response.dataset;
  },
  analyzeAI: (payload: Record<string, unknown>) =>
    apiRequest<AIAnalysisRecord>('/ai/analyze', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  followupAI: (payload: Record<string, unknown>) =>
    apiRequest<{ status: string; answer: string }>('/ai/followup', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  sendFeishu: (payload: Record<string, unknown>) =>
    apiRequest<{ sent: boolean; reason?: string; response?: unknown }>('/notifications/feishu', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};

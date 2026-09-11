import type {
  AIAnalysisRecord,
  BatchAnalyzeResponse,
  DatasetSummary,
  HealthSummary,
  MonitorStatus,
  PaAnalysisResult,
  SrAnalysisResult,
  SyncDatasetResponse,
  SystemConfig,
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
  getSystemConfig: () =>
    apiRequest<{ status: string; config: SystemConfig }>('/system/config').then(
      (payload) => payload.config,
    ),
  saveSystemConfig: (payload: Partial<SystemConfig>) =>
    apiRequest<{ status: string; config: SystemConfig }>('/system/config', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }).then((result) => result.config),
  resetSystemConfig: () =>
    apiRequest<{ status: string; config: SystemConfig }>('/system/config/reset', {
      method: 'POST',
    }).then((result) => result.config),
  testFeishu: (payload: Record<string, unknown> = {}) =>
    apiRequest<{ sent: boolean; reason?: string; response?: unknown }>(
      '/system/config/test-feishu',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),
  batchAnalyze: (payload: Record<string, unknown>) =>
    apiRequest<BatchAnalyzeResponse>('/ai/batch/analyze', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  startMonitor: (payload: Record<string, unknown>) =>
    apiRequest<MonitorStatus>('/ai/monitor/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  stopMonitor: () => apiRequest<MonitorStatus>('/ai/monitor/stop', { method: 'POST' }),
  getMonitorStatus: () => apiRequest<MonitorStatus>('/ai/monitor/status'),
  runMonitorOnce: () =>
    apiRequest<{ status: string; items: Array<Record<string, unknown>>; cycle_at?: string }>(
      '/ai/monitor/run-once',
      { method: 'POST' },
    ),
};

export async function streamAIAnalysis(
  payload: Record<string, unknown>,
  onEvent: (event: Record<string, any>) => void,
  signal?: AbortSignal,
): Promise<AIAnalysisRecord> {
  const response = await fetch(`${API_BASE}/ai/analyze/stream`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `流式分析请求失败（${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalRecord: AIAnalysisRecord | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    for (const chunk of chunks) {
      const data = chunk
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trim())
        .join('');
      if (!data) continue;
      let event: Record<string, any>;
      try {
        event = JSON.parse(data) as Record<string, any>;
      } catch {
        continue;
      }
      onEvent(event);
      if (event.type === 'error') throw new Error(event.message ?? '模型分析失败');
      if (event.type === 'done' && event.record) finalRecord = event.record as AIAnalysisRecord;
    }
  }
  if (!finalRecord) throw new Error('流式分析结束但未返回完整记录');
  return finalRecord;
}

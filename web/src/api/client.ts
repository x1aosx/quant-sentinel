import type {
  AIAnalysisRecord,
  AIRecordDetailResponse,
  AIRecordListResponse,
  AnalysisInstrumentSummariesParams,
  AnalysisInstrumentSummariesResponse,
  BatchAnalyzeResponse,
  DatasetSummary,
  DiscoveryCandidateListResponse,
  HealthSummary,
  IntelligenceOverview,
  IntelligenceRunRequest,
  IntelligenceRunResult,
  IntelligenceThemeCategory,
  MarketEventListResponse,
  MonitorStatus,
  MorningBriefResponse,
  ProviderTestResult,
  ScheduleDefinition,
  SchedulerTrigger,
  StockAnalysisAISummary,
  StockAnalysisResponse,
  SyncDatasetResponse,
  SystemConfig,
  TaskDefinition,
  TaskExecution,
  ThemeListResponse,
  UnifiedAnalysisResult,
  WorkerSummary,
} from '../types';

const API_BASE = '/api/v1';

function errorMessage(payload: unknown, status: number): string {
  if (typeof payload === 'string' && payload.trim()) return payload;
  if (payload && typeof payload === 'object') {
    const body = payload as { detail?: unknown; message?: unknown };
    if (typeof body.detail === 'string' && body.detail.trim()) return body.detail;
    if (Array.isArray(body.detail)) {
      const details = body.detail
        .map((item) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object' && 'msg' in item) {
            return String((item as { msg?: unknown }).msg ?? '');
          }
          return '';
        })
        .filter(Boolean);
      if (details.length) return details.join('；');
    }
    if (typeof body.message === 'string' && body.message.trim()) return body.message;
  }
  return `请求失败（${status}）`;
}

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
    throw new Error(errorMessage(payload, response.status));
  }
  return payload as T;
}

export const api = {
  getHealth: () => apiRequest<HealthSummary>('/health'),
  getIntelligenceOverview: () =>
    apiRequest<IntelligenceOverview>('/intelligence/overview'),
  runIntelligence: (payload: IntelligenceRunRequest = {}) =>
    apiRequest<IntelligenceRunResult>('/intelligence/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listMarketEvents: (limit = 50) =>
    apiRequest<MarketEventListResponse>(
      `/intelligence/events?${new URLSearchParams({ limit: String(limit) }).toString()}`,
    ),
  listThemes: (params: { limit?: number; category?: IntelligenceThemeCategory } = {}) => {
    const search = new URLSearchParams({ limit: String(params.limit ?? 50) });
    if (params.category) search.set('category', params.category);
    return apiRequest<ThemeListResponse>(`/themes?${search.toString()}`);
  },
  getMorningBrief: () => apiRequest<MorningBriefResponse>('/brief/morning'),
  listDiscoveryCandidates: (
    params: { state?: string; limit?: number } = {},
  ) => {
    const search = new URLSearchParams({
      state: params.state ?? 'all',
      limit: String(params.limit ?? 100),
    });
    return apiRequest<DiscoveryCandidateListResponse>(
      `/discovery/candidates?${search.toString()}`,
    );
  },
  refreshDiscovery: () =>
    apiRequest<DiscoveryCandidateListResponse>('/discovery/refresh', {
      method: 'POST',
    }),
  getDashboard: () => apiRequest<{
    status: string;
    dataset_count: number;
    latest_dataset?: DatasetSummary | null;
    mode: string;
  }>('/dashboard'),
  listDatasets: () => apiRequest<{ items: DatasetSummary[] }>('/datasets'),
  getStockAnalysis: (
    symbol: string,
    options: { refresh?: boolean; include_ai?: boolean } = {},
  ) => {
    const search = new URLSearchParams({
      refresh: String(options.refresh ?? false),
      include_ai: String(options.include_ai ?? false),
    });
    return apiRequest<StockAnalysisResponse>(
      `/stocks/${encodeURIComponent(symbol)}/analysis?${search.toString()}`,
    );
  },
  generateStockAnalysisAI: (symbol: string) =>
    apiRequest<StockAnalysisAISummary | StockAnalysisResponse>(
      `/stocks/${encodeURIComponent(symbol)}/analysis/ai`,
      { method: 'POST' },
    ),
  getInstrumentSummaries: (params: AnalysisInstrumentSummariesParams = {}) => {
    const search = new URLSearchParams({
      page: String(params.page ?? 1),
      page_size: String(params.page_size ?? 20),
    });
    if (params.keyword) search.set('keyword', params.keyword);
    if (params.timeframe) search.set('timeframe', params.timeframe);
    if (params.trend) search.set('trend', params.trend);
    if (params.change) search.set('change', params.change);
    if (params.refresh) search.set('refresh', 'true');
    return apiRequest<AnalysisInstrumentSummariesResponse>(
      `/analysis/instruments?${search.toString()}`,
    );
  },
  deleteDataset: (datasetId: string) =>
    apiRequest<{ deleted: boolean; id: string }>(`/datasets/${encodeURIComponent(datasetId)}`, {
      method: 'DELETE',
    }),
  analyzeSupportResistance: (payload: {
    dataset_id?: string;
    symbol?: string;
    timeframe?: string;
    lookback?: number;
    n_zones?: number;
    direction?: 'both' | 'long' | 'short';
    risk_fraction?: number;
    min_rr?: number;
    stance?: 'conservative' | 'balanced' | 'aggressive';
  }) =>
    apiRequest<UnifiedAnalysisResult>('/analysis/support-resistance', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  syncRemoteDataset: (payload: {
    source: 'yfinance' | 'akshare' | 'tradingview' | 'mt5';
    symbol: string;
    timeframe: string;
    lookback: number;
    adjust: 'qfq' | 'hfq' | 'none';
    exchange?: string;
  }) =>
    apiRequest<SyncDatasetResponse>('/datasets/remote/sync', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importRemoteDataset: async (payload: {
    source: 'yfinance' | 'akshare' | 'tradingview' | 'mt5';
    symbol: string;
    timeframe: string;
    lookback?: number;
    adjust?: 'qfq' | 'hfq' | 'none';
    exchange?: string;
  }) => {
    const response = await api.syncRemoteDataset({
      source: payload.source,
      symbol: payload.symbol,
      timeframe: payload.timeframe,
      lookback: payload.lookback ?? 500,
      adjust: payload.adjust ?? 'qfq',
      exchange: payload.exchange,
    });
    return response.dataset;
  },
  analyzeAI: (payload: Record<string, unknown>) =>
    apiRequest<AIAnalysisRecord>('/ai/analyze', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listAIRecords: (
    params: {
      dataset_id?: string;
      symbol?: string;
      timeframe?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams({
      limit: String(params.limit ?? 50),
      offset: String(params.offset ?? 0),
    });
    if (params.dataset_id) search.set('dataset_id', params.dataset_id);
    if (params.symbol) search.set('symbol', params.symbol);
    if (params.timeframe) search.set('timeframe', params.timeframe);
    return apiRequest<AIRecordListResponse>(`/ai/records?${search.toString()}`).then(
      (payload) => ({
        ...payload,
        items: payload.items.map((item) => ({
          ...item,
          action: item.action ?? item.decision_action ?? null,
        })),
      }),
    );
  },
  getAIRecord: async (recordId: string) => {
    const payload = await apiRequest<AIAnalysisRecord | AIRecordDetailResponse>(
      `/ai/records/${encodeURIComponent(recordId)}`,
    );
    if (!('record' in payload)) return payload;
    return {
      ...payload.record,
      id: payload.record.id ?? payload.record_id ?? payload.id ?? recordId,
      dataset_id: payload.record.dataset_id ?? payload.dataset_id ?? undefined,
      persisted_at: payload.record.persisted_at ?? payload.created_at ?? undefined,
    };
  },
  deleteAIRecord: (recordId: string) =>
    apiRequest<{ deleted: boolean; id: string }>(
      `/ai/records/${encodeURIComponent(recordId)}`,
      { method: 'DELETE' },
    ),
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
  testProvider: (payload: Record<string, unknown> = {}) =>
    apiRequest<ProviderTestResult>('/system/config/test-provider', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
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
  listSchedulerTasks: () =>
    apiRequest<{ items: TaskDefinition[] }>('/scheduler/tasks').then((payload) => payload.items),
  runSchedulerTask: (
    taskName: string,
    payload: { params?: Record<string, unknown>; priority?: number } = {},
  ) =>
    apiRequest<TaskExecution>(`/scheduler/tasks/${encodeURIComponent(taskName)}/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listSchedules: (params: { enabled?: boolean } = {}) => {
    const search = new URLSearchParams();
    if (params.enabled !== undefined) search.set('enabled', String(params.enabled));
    const query = search.toString();
    return apiRequest<{ items: ScheduleDefinition[] }>(
      `/scheduler/schedules${query ? `?${query}` : ''}`,
    ).then((payload) => payload.items);
  },
  createSchedule: (payload: {
    id: string;
    task_name: string;
    trigger: SchedulerTrigger;
    params: Record<string, unknown>;
    timezone: string;
    misfire_policy: ScheduleDefinition['misfire_policy'];
    enabled: boolean;
    max_catch_up_runs: number;
  }) =>
    apiRequest<ScheduleDefinition>('/scheduler/schedules', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateSchedule: (
    scheduleId: string,
    payload: Partial<ScheduleDefinition> & { trigger?: SchedulerTrigger },
  ) =>
    apiRequest<ScheduleDefinition>(
      `/scheduler/schedules/${encodeURIComponent(scheduleId)}`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      },
    ),
  deleteSchedule: (scheduleId: string) =>
    apiRequest<{ deleted: boolean }>(
      `/scheduler/schedules/${encodeURIComponent(scheduleId)}`,
      { method: 'DELETE' },
    ),
  pauseSchedule: (scheduleId: string) =>
    apiRequest<ScheduleDefinition>(
      `/scheduler/schedules/${encodeURIComponent(scheduleId)}/pause`,
      { method: 'POST' },
    ),
  resumeSchedule: (scheduleId: string) =>
    apiRequest<ScheduleDefinition>(
      `/scheduler/schedules/${encodeURIComponent(scheduleId)}/resume`,
      { method: 'POST' },
    ),
  listExecutions: (
    params: { status?: string; task_name?: string; limit?: number; offset?: number } = {},
  ) => {
    const search = new URLSearchParams({ limit: String(params.limit ?? 100) });
    if (params.status) search.set('status', params.status);
    if (params.task_name) search.set('task_name', params.task_name);
    if (params.offset !== undefined) search.set('offset', String(params.offset));
    return apiRequest<{ items: TaskExecution[] }>(`/scheduler/executions?${search.toString()}`).then(
      (payload) => payload.items,
    );
  },
  getExecution: (executionId: string) =>
    apiRequest<TaskExecution>(`/scheduler/executions/${encodeURIComponent(executionId)}`),
  retryExecution: (executionId: string) =>
    apiRequest<TaskExecution>(
      `/scheduler/executions/${encodeURIComponent(executionId)}/retry`,
      { method: 'POST' },
    ),
  cancelExecution: (executionId: string) =>
    apiRequest<TaskExecution>(
      `/scheduler/executions/${encodeURIComponent(executionId)}/cancel`,
      { method: 'POST' },
    ),
  listSchedulerWorkers: () =>
    apiRequest<{ items: WorkerSummary[] }>('/scheduler/workers').then((payload) => payload.items),
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
    const raw = await response.text().catch(() => '');
    let detail = '';
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown } | null;
      if (typeof parsed?.detail === 'string') detail = parsed.detail;
    } catch {
      // Not JSON (e.g. a plain-text 500) — fall back to the body snippet below.
    }
    throw new Error(detail || raw.trim().slice(0, 300) || `流式分析请求失败（${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalRecord: AIAnalysisRecord | null = null;
  let streamError = '';
  const consumeEvent = (chunk: string) => {
    const data = chunk
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');
    if (!data) return;
    let event: Record<string, any>;
    try {
      event = JSON.parse(data) as Record<string, any>;
    } catch {
      return;
    }
    onEvent(event);
    if (event.type === 'error') {
      streamError = event.message ?? '模型分析失败';
    }
    if (event.type === 'done' && event.record) {
      finalRecord = event.record as AIAnalysisRecord;
    }
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    chunks.forEach(consumeEvent);
  }
  buffer += decoder.decode().replace(/\r\n/g, '\n');
  if (buffer.trim()) consumeEvent(buffer);
  if (!finalRecord) {
    throw new Error(streamError || '流式分析结束但未返回完整记录');
  }
  return finalRecord;
}

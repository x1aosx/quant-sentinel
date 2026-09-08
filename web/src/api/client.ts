import type {
  DashboardSummary,
  DataStatus,
  Experiment,
  HealthSummary,
  Instance,
  NotificationItem,
  Position,
  ReplayRun,
  RiskSummary,
  StrategyDetail,
  StrategySummary,
} from '../types';

const API_BASE = '/api/v1';

const demoStrategies: StrategySummary[] = [
  {
    id: 'xq.srpa.breakout_retest.long',
    version: '0.1.0',
    title: '支撑阻力突破回测做多',
    research_status: 'SPECIFIED',
    implementation_status: 'NOT_IMPLEMENTED',
    evidence_level: 'E1',
    market: 'CN_A_EQUITY',
    frequency: '1d',
    source_refs: ['R03', 'R08', 'R12', 'R15'],
    tags: ['S-BR', 'long_only', 'next_session'],
    updated_at: '2026-09-08T10:00:00+08:00',
  },
  {
    id: 'xq.baseline.cash',
    version: '0.1.0',
    title: '现金基线',
    research_status: 'SPECIFIED',
    implementation_status: 'NOT_IMPLEMENTED',
    evidence_level: 'E1',
    market: 'CN_A_EQUITY',
    frequency: '1d',
    source_refs: [],
    tags: ['baseline', 'no_position'],
    updated_at: '2026-09-08T10:00:00+08:00',
  },
  {
    id: 'xq.baseline.ema_20_60.long',
    version: '0.1.0',
    title: 'EMA 20/60 趋势基线',
    research_status: 'SPECIFIED',
    implementation_status: 'NOT_IMPLEMENTED',
    evidence_level: 'E1',
    market: 'CN_A_EQUITY',
    frequency: '1d',
    source_refs: ['R04'],
    tags: ['baseline', 'ema', 'long_only'],
    updated_at: '2026-09-08T10:00:00+08:00',
  },
  {
    id: 'xq.baseline.donchian_20_10.long',
    version: '0.1.0',
    title: 'Donchian 通道基线',
    research_status: 'SPECIFIED',
    implementation_status: 'NOT_IMPLEMENTED',
    evidence_level: 'E1',
    market: 'CN_A_EQUITY',
    frequency: '1d',
    source_refs: ['R19'],
    tags: ['baseline', 'donchian', 'long_only'],
    updated_at: '2026-09-08T10:00:00+08:00',
  },
  {
    id: 'xq.srpa.h2_pullback.long',
    version: '0.1.0',
    title: 'H2 二次回撤做多',
    research_status: 'SPECIFIED',
    implementation_status: 'NOT_IMPLEMENTED',
    evidence_level: 'E1',
    market: 'CN_A_EQUITY',
    frequency: '1d',
    source_refs: ['R06', 'R09'],
    tags: ['S-H2', 'optional_setup', 'default_off'],
    updated_at: '2026-09-08T10:00:00+08:00',
  },
  {
    id: 'xq.challenger.boll_cci.long',
    version: '0.1.0',
    title: 'Boll/CCI 挑战者',
    research_status: 'SPECIFIED',
    implementation_status: 'NOT_IMPLEMENTED',
    evidence_level: 'E1',
    market: 'CN_A_EQUITY',
    frequency: '1d',
    source_refs: ['R20'],
    tags: ['challenger', 'boll', 'cci'],
    updated_at: '2026-09-08T10:00:00+08:00',
  },
];

const demoExperiments: Experiment[] = [
  {
    id: 'exp-001',
    title: 'S-BR 合成夹具四组回放',
    strategy_id: 'xq.srpa.breakout_retest.long',
    status: 'SUCCEEDED',
    progress: 100,
    created_at: '2026-09-08T09:10:00+08:00',
    started_at: '2026-09-08T09:10:02+08:00',
    finished_at: '2026-09-08T09:10:31+08:00',
    comparison_runs: ['run-demo-sbr', 'run-demo-cash'],
    tags: ['replay', 'synthetic', 'demo'],
  },
  {
    id: 'exp-002',
    title: 'EMA 基线同快照比较',
    strategy_id: 'xq.baseline.ema_20_60.long',
    status: 'RUNNING',
    progress: 64,
    created_at: '2026-09-08T09:12:00+08:00',
    started_at: '2026-09-08T09:12:05+08:00',
    comparison_runs: ['run-demo-ema'],
    tags: ['baseline', 'demo'],
  },
  {
    id: 'exp-003',
    title: '参数网格 24 组预注册',
    strategy_id: 'xq.srpa.breakout_retest.long',
    status: 'FAILED',
    progress: 12,
    created_at: '2026-09-08T08:40:00+08:00',
    started_at: '2026-09-08T08:40:04+08:00',
    finished_at: '2026-09-08T08:41:10+08:00',
    comparison_runs: [],
    failure_reason: 'dataset_manifest_hash missing for trial grid-07',
    tags: ['parameter_search', 'blocked'],
  },
  {
    id: 'exp-004',
    title: '未来数据泄漏检查',
    strategy_id: 'xq.srpa.breakout_retest.long',
    status: 'QUEUED',
    progress: 0,
    created_at: '2026-09-08T09:20:00+08:00',
    comparison_runs: [],
    tags: ['validation', 'lookahead'],
  },
];

const demoDashboard: DashboardSummary = {
  mode: 'demo',
  demo: true,
  running_instances: 0,
  data_status: 'DEMO_SNAPSHOT',
  data_last_updated: '2026-09-08T09:30:00+08:00',
  risk_status: 'OK',
  pending_plans: 0,
  pending_approvals: 0,
  recent_experiments: demoExperiments.slice(0, 3),
  heartbeat_at: '2026-09-08T09:30:00+08:00',
};

const demoInstances: Instance[] = [
  {
    id: 'inst-001',
    strategy_id: 'xq.srpa.breakout_retest.long',
    version: '0.1.0',
    account: 'research-demo',
    universe: ['DEMO.EXAMPLE'],
    mode: 'demo',
    status: 'STOPPED',
    budget: { equity: 100000, risk_fraction: 0.0025, max_symbols: 8, max_sector_exposure: 0.25 },
    last_heartbeat: '2026-09-08T09:30:00+08:00',
    approvals: [],
  },
];

const demoPositions: Position[] = [
  {
    instrument_id: 'DEMO.EXAMPLE',
    quantity: 200,
    available_quantity: 200,
    avg_cost: 10.05,
    market_value: 2010,
    unrealized_pnl: -12,
    sector: 'DEMO',
    protection_threshold: 9.4,
    risk_status: 'MANUAL_UNASSIGNED',
  },
];

const demoRisk: RiskSummary = {
  cash: 97900,
  equity: 99900,
  gross_exposure: 0.0201,
  gross_exposure_limit: 0.8,
  sector_exposure: [{ sector: 'DEMO', weight: 0.0201, limit: 0.25 }],
  limits: [
    { name: '单笔计划风险', current: 0.00186, limit: 0.0025, unit: 'equity', status: 'OK' },
    { name: '总多头市值', current: 0.0201, limit: 0.8, unit: 'equity', status: 'OK' },
    { name: '持仓股票数', current: 1, limit: 8, unit: 'symbols', status: 'OK' },
  ],
  flags: ['DEMO 合成数据，不可用于真实部署'],
};

const demoDataStatus: DataStatus[] = [
  {
    source: 'eastmoney',
    coverage: 0,
    last_updated: '2026-09-08T09:30:00+08:00',
    quality_flags: ['DEMO', 'VINTAGE_UNVERIFIED'],
    revision: 'demo-snapshot',
    snapshots: 1,
  },
  {
    source: 'gate',
    coverage: 0,
    last_updated: '2026-09-08T09:30:00+08:00',
    quality_flags: ['DEMO', 'UNAUTHORIZED'],
    revision: 'none',
    snapshots: 0,
  },
];

const demoNotifications: NotificationItem[] = [
  {
    id: 'ntf-001',
    kind: 'system',
    title: '数据快照为空',
    body: '当前只有合成夹具，无法生成真实市场信号。',
    status: 'DELIVERED',
    delivered_at: '2026-09-08T09:31:00+08:00',
    feedback: 'ACKNOWLEDGED',
    links: ['/data'],
  },
];

const demoHealth: HealthSummary = {
  status: 'degraded',
  version: '0.1.0',
  heartbeat_at: '2026-09-08T09:30:00+08:00',
  data_last_updated: '2026-09-08T09:30:00+08:00',
  queue_depth: 1,
  task_heartbeat_ok: true,
};

const demoReplay: ReplayRun = {
  run_id: 'run-demo-sbr',
  strategy_id: 'xq.srpa.breakout_retest.long',
  instrument_id: 'DEMO.EXAMPLE',
  snapshot_id: 'demo-snapshot',
  status: 'SUCCEEDED',
  started_at: '2026-09-08T09:10:02+08:00',
  events: [
    { session: '2026-09-01', kind: 'BreakoutSeen', instrument_id: 'DEMO.EXAMPLE', state: 'BREAKOUT_SEEN', reason_codes: ['CLOSE_ABOVE_ZONE'] },
    { session: '2026-09-02', kind: 'HeldAbove', instrument_id: 'DEMO.EXAMPLE', state: 'HELD_ABOVE', reason_codes: ['CLOSE_ABOVE_ZONE'] },
    { session: '2026-09-03', kind: 'RetestSeen', instrument_id: 'DEMO.EXAMPLE', state: 'RETEST_SEEN', reason_codes: ['LOW_INTO_ZONE'] },
    { session: '2026-09-04', kind: 'Confirmed', instrument_id: 'DEMO.EXAMPLE', state: 'CONFIRMED', reason_codes: ['BULLISH_CLOSE', 'CLV_OK'] },
    { session: '2026-09-05', kind: 'PlanPublished', instrument_id: 'DEMO.EXAMPLE', state: 'PLANNED', reason_codes: ['RISK_BUDGET_OK'] },
    { session: '2026-09-08', kind: 'ReferenceFill', instrument_id: 'DEMO.EXAMPLE', state: 'FILLED', reason_codes: ['NEXT_OPEN_REFERENCE'] },
  ],
  trade_plans: [
    {
      plan_id: 'plan-demo-001',
      instrument_id: 'DEMO.EXAMPLE',
      status: 'FILLED',
      entry_min: '9.88',
      entry_max: '10.25',
      stop_threshold: '9.40',
      target_threshold: '11.40',
      reason_codes: ['BREAKOUT_RETEST_CONFIRMED', 'RISK_BUDGET_OK'],
    },
  ],
  equity_curve: [
    { session: '2026-09-01', equity: 100000, drawdown: 0 },
    { session: '2026-09-02', equity: 100000, drawdown: 0 },
    { session: '2026-09-03', equity: 100000, drawdown: 0 },
    { session: '2026-09-04', equity: 100000, drawdown: 0 },
    { session: '2026-09-05', equity: 100000, drawdown: 0 },
    { session: '2026-09-08', equity: 99900, drawdown: -0.001 },
  ],
};

function demoStrategyDetail(id: string): StrategyDetail {
  const summary = demoStrategies.find((s) => s.id === id) ?? demoStrategies[0];
  return {
    ...summary,
    assumptions: [
      '具有明确上行结构、流动性足够的股票，突破既有阻力后回测并出现收盘确认，可能比任意追涨有更清晰的失效位置。',
      '相对强弱只决定候选优先级，不替代入场。',
      '默认只做多、无杠杆、无摊平、无金字塔加仓。',
    ],
    parameter_schema: [
      { key: 'breakout.close_buffer_atr', type: 'number', default: 0.2, min: 0, max: 1, description: '突破收盘高于区域上沿的 ATR 倍数' },
      { key: 'setup.maximum_setup_sessions', type: 'integer', default: 12, min: 3, max: 30, description: '形态最长会话数' },
      { key: 'entry.minimum_net_reward_risk', type: 'number', default: 1.5, min: 1, max: 5, description: '净结构盈亏比下限' },
      { key: 'portfolio.risk_fraction_per_trade', type: 'number', default: 0.0025, min: 0.0001, max: 0.02, description: '每笔计划风险预算占净资产比例' },
    ],
    versions: [
      { version: '0.1.0', config_hash: 'DEMO-NOT-A-REAL-HASH', created_at: '2026-09-08T10:00:00+08:00', status: 'SPECIFIED', changes: ['初始规格化'] },
    ],
    related_experiments: demoExperiments.filter((e) => e.strategy_id === id).map((e) => ({ id: e.id, title: e.title, status: e.status })),
    deployments: demoInstances.filter((i) => i.strategy_id === id).map((i) => ({ id: i.id, instance_id: i.id, version: i.version, mode: i.mode, status: i.status })),
  };
}

async function apiFetch<T>(path: string, fallback: T): Promise<T> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(`${API_BASE}${path}`, { signal: controller.signal, headers: { Accept: 'application/json' } });
    clearTimeout(timer);
    if (!res.ok) {
      return fallback;
    }
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

async function apiSend<T>(path: string, body: unknown, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      return fallback;
    }
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export const api = {
  getDashboard: () => apiFetch<DashboardSummary>('/dashboard', demoDashboard),
  getStrategies: async () => {
    const data = await apiFetch<{ items: StrategySummary[] }>('/strategies', { items: demoStrategies });
    return data.items;
  },
  getStrategy: async (id: string) => {
    const data = await apiFetch<StrategyDetail>(`/strategies/${encodeURIComponent(id)}`, demoStrategyDetail(id));
    return data;
  },
  getExperiments: async () => {
    const data = await apiFetch<{ items: Experiment[] }>('/experiments', { items: demoExperiments });
    return data.items;
  },
  getExperiment: async (id: string) => {
    const data = await apiFetch<Experiment>(`/experiments/${encodeURIComponent(id)}`, demoExperiments.find((e) => e.id === id) ?? demoExperiments[0]);
    return data;
  },
  getInstances: async () => {
    const data = await apiFetch<{ items: Instance[] }>('/instances', { items: demoInstances });
    return data.items;
  },
  getPlans: async () => {
    const data = await apiFetch<{ items: unknown[] }>('/plans', { items: [] });
    return data.items;
  },
  getHealth: () => apiFetch<HealthSummary>('/health', demoHealth),
  getPositions: async () => {
    const data = await apiFetch<{ items: Position[] }>('/positions', { items: demoPositions });
    return data.items;
  },
  getRisk: () => apiFetch<RiskSummary>('/risk', demoRisk),
  getDataStatus: async () => {
    const data = await apiFetch<{ items: DataStatus[] }>('/data', { items: demoDataStatus });
    return data.items;
  },
  getNotifications: async () => {
    const data = await apiFetch<{ items: NotificationItem[] }>('/notifications', { items: demoNotifications });
    return data.items;
  },
  getEvents: async () => {
    const data = await apiFetch<{ items: unknown[] }>('/events', { items: [] });
    return data.items;
  },
  startReplay: (payload: { strategy_id: string; instrument_id: string; snapshot_id: string }) =>
    apiSend<ReplayRun>('/replay', payload, { ...demoReplay, run_id: `run-${Date.now()}`, ...payload }),
  instanceCommand: (id: string, command: string) =>
    apiSend<{ id: string; command: string; status: string }>(`/instances/${encodeURIComponent(id)}/commands`, { command }, { id, command, status: 'ACCEPTED' }),
  planFeedback: (id: string, feedback: string) =>
    apiSend<{ id: string; feedback: string; status: string }>(`/plans/${encodeURIComponent(id)}/feedback`, { feedback }, { id, feedback, status: 'ACCEPTED' }),
};

export type Mode = 'research' | 'paper' | 'live_assist' | 'demo';

export interface DatasetBar {
  session_id: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface DatasetSummary {
  id: string;
  symbol: string;
  timeframe: string;
  bar_count: number;
  first_session: string;
  last_session: string;
  created_at: string;
}

export interface SrLevel {
  zone_type: 'support' | 'resistance';
  center: number;
  low: number;
  high: number;
  distance_pct?: number;
  distance_atr?: number;
  width_atr?: number;
  edge_score?: number;
  n_events?: number;
  touch_count?: number;
  volume_pct?: number;
  tf_count?: number;
  tfs?: string;
}

export interface SrAnalysisResult {
  symbol: string;
  timeframe: string;
  bars_used: number;
  current_price: number;
  atr: number;
  atr_pct: number;
  trend: { label: string; detail?: string };
  levels: SrLevel[];
  summary: {
    headline?: string;
    nearest?: SrLevel | null;
    best?: SrLevel | null;
    risk_reward?: {
      risk_reward_ratio?: number;
      quality?: string;
      potential_profit_pct?: number;
      potential_loss_pct?: number;
    };
    caveat?: string;
  };
  meta?: Record<string, unknown>;
  candles: DatasetBar[];
}

export interface PaAnalysisResult {
  symbol: string;
  timeframe: string;
  bars_used: number;
  current_price: number;
  ema20: number;
  atr: number;
  atr_pct: number;
  market_context: {
    direction: 'bullish' | 'bearish' | 'neutral';
    cycle_position?: string;
    price_position?: number;
    range_high?: number | null;
    range_low?: number | null;
    overlap_mean_10?: number | null;
    trend_detail?: string;
    background_direction?: string;
    recent_spike?: string | null;
    scale_conflict?: boolean;
  };
  features: {
    swing_structure?: string;
    swings?: Array<{ seq?: number; kind?: string; price?: number }>;
    breakout_quality?: string;
    breakout_events?: Array<Record<string, unknown>>;
    patterns?: string[];
    supports?: number[];
    resistances?: number[];
  };
  decision: {
    action: 'LONG' | 'SHORT' | 'WAIT';
    confidence: number;
    entry?: number | null;
    stop?: number | null;
    target?: number | null;
    rr?: number | null;
    risk_fraction?: number;
    reason_codes?: string[];
    reasoning?: string;
    invalidation?: string | null;
  };
  meta?: Record<string, unknown>;
  candles: DatasetBar[];
  levels: SrLevel[];
}

export interface DashboardSummary {
  mode: Mode;
  demo: boolean;
  running_instances: number;
  data_status: string;
  data_last_updated: string;
  risk_status: 'OK' | 'WATCH' | 'BREACH';
  pending_plans: number;
  pending_approvals: number;
  recent_experiments: Experiment[];
  heartbeat_at: string;
}

export interface StrategySummary {
  id: string;
  version: string;
  title: string;
  research_status: string;
  implementation_status: string;
  evidence_level: string;
  market: string;
  frequency: string;
  source_refs: string[];
  tags: string[];
  updated_at: string;
}

export interface ParameterSchemaSummary {
  key: string;
  type: string;
  default?: string | number | boolean;
  min?: number;
  max?: number;
  description: string;
}

export interface StrategyVersion {
  version: string;
  config_hash: string;
  created_at: string;
  status: string;
  changes: string[];
}

export interface StrategyDetail extends StrategySummary {
  assumptions: string[];
  parameter_schema: ParameterSchemaSummary[];
  versions: StrategyVersion[];
  related_experiments: Array<{ id: string; title: string; status: string }>;
  deployments: Array<{ id: string; instance_id: string; version: string; mode: Mode; status: string }>;
}

export interface Experiment {
  id: string;
  title: string;
  strategy_id: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';
  progress: number;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  comparison_runs: string[];
  failure_reason?: string;
  tags: string[];
}

export interface ReplayRun {
  run_id: string;
  strategy_id: string;
  instrument_id: string;
  snapshot_id: string;
  status: string;
  started_at: string;
  events: Array<{
    session: string;
    kind: string;
    instrument_id: string;
    state?: string;
    reason_codes?: string[];
  }>;
  trade_plans: Array<{
    plan_id: string;
    instrument_id: string;
    status: string;
    entry_min: string;
    entry_max: string;
    stop_threshold: string;
    target_threshold: string;
    reason_codes: string[];
  }>;
  equity_curve: Array<{
    session: string;
    equity: number;
    drawdown: number;
  }>;
}

export interface Instance {
  id: string;
  strategy_id: string;
  version: string;
  account: string;
  universe: string[];
  mode: Mode;
  status: string;
  budget: {
    equity: number;
    risk_fraction: number;
    max_symbols: number;
    max_sector_exposure: number;
  };
  last_heartbeat: string;
  approvals: Array<{
    id: string;
    version: string;
    approved_at: string;
    report_ref: string;
  }>;
}

export interface Position {
  instrument_id: string;
  quantity: number;
  available_quantity: number;
  avg_cost: number;
  market_value: number;
  unrealized_pnl: number;
  sector: string;
  protection_threshold?: number;
  risk_status: string;
}

export interface RiskSummary {
  cash: number;
  equity: number;
  gross_exposure: number;
  gross_exposure_limit: number;
  sector_exposure: Array<{ sector: string; weight: number; limit: number }>;
  limits: Array<{ name: string; current: number; limit: number; unit: string; status: string }>;
  flags: string[];
}

export interface DataStatus {
  source: string;
  coverage: number;
  last_updated: string;
  quality_flags: string[];
  revision: string;
  snapshots: number;
  delay_minutes?: number;
}

export interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string;
  status: string;
  delivered_at: string;
  feedback: string;
  links: string[];
}

export interface HealthSummary {
  status: string;
  version: string;
  mode: string;
  dataset_count: number;
}

export interface AIProviderConfig {
  model: string;
  base_url: string;
  api_key: string;
  thinking: boolean;
  reasoning_effort: string;
  context_window: number;
}

export interface AIAnalysisRecord {
  id: string;
  status: string;
  symbol: string;
  timeframe: string;
  snapshot: Record<string, any>;
  stage1_messages: Array<{ role: string; content: string }>;
  stage2_messages: Array<{ role: string; content: string }>;
  raw_prompt: Record<string, unknown>;
  stage1_response: string;
  stage2_response: string;
  stage1_diagnosis?: Record<string, any>;
  stage2_decision?: Record<string, any>;
  decision_tree_layout?: {
    nodes: Array<{
      id: string;
      label: string;
      question: string;
      answer: string;
      x: number;
      y: number;
    }>;
    edges: Array<{ from: string; to: string }>;
  };
  exception?: Record<string, any> | null;
}

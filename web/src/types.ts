export type Mode = 'research' | 'paper' | 'live_assist' | 'demo';

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
  heartbeat_at: string;
  data_last_updated: string;
  queue_depth: number;
  task_heartbeat_ok: boolean;
}

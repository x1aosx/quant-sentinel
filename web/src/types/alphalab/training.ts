import type { AlphaLabStatus } from './common';

export interface TrainingMetrics {
  step?: number;
  reward?: number;
  validation_score?: number;
  ic?: number;
  rank_ic?: number;
  entropy?: number;
  best_score?: number;
  elite_pool_size?: number;
  invalid_rate?: number;
  loss?: number;
  turnover?: number;
  [key: string]: unknown;
}

/** One sampled point of the training curve; the backend keeps the full series. */
export interface TrainingMetricsPoint extends TrainingMetrics {
  step: number;
  ts?: string;
}

export interface TrainingLogEntry {
  ts: string;
  level: string;
  step?: number | null;
  message: string;
}

export interface TrainingRun {
  id: string;
  name?: string;
  market?: string;
  symbol?: string;
  symbols?: string[];
  timeframe: string;
  dataset_id?: string;
  dataset_title?: string;
  data_snapshot_id?: string;
  factor_schema_version?: string;
  config_profile?: string;
  config_json?: Record<string, unknown>;
  seed?: number;
  status: AlphaLabStatus;
  progress?: number;
  step?: number;
  current_step?: number;
  total_steps?: number;
  best_strategy_id?: string | null;
  best_formula?: string | null;
  best_formula_tokens?: number[];
  best_score?: number | null;
  metrics_json?: TrainingMetrics;
  metrics_history?: TrainingMetricsPoint[];
  logs?: TrainingLogEntry[];
  checkpoint_uri?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  code_version?: string | null;
  upstream_baseline?: string | null;
  worker_id?: string | null;
  error_code?: string | null;
  error?: string | null;
  error_message?: string | null;
  created_by?: string | null;
  candidates?: TrainingCandidate[];
}

export interface TrainingCandidate {
  id?: string;
  strategy_id?: string;
  rank?: number;
  formula?: string;
  formula_expression?: string;
  score?: number;
  validation_score?: number;
  train_score?: number;
  metrics_json?: Record<string, unknown>;
  status?: AlphaLabStatus;
}

export interface TrainingRunCreateRequest {
  name?: string;
  symbols: string[];
  timeframe: string;
  data_snapshot_id?: string;
  config_profile?: string;
  seed?: number;
  from_scratch?: boolean;
  total_steps?: number;
  batch_size?: number;
  device?: 'auto' | 'cpu' | 'cuda' | 'mps';
}

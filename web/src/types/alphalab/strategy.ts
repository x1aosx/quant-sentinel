import type { AlphaLabStatus, AlphaLabTimeRange } from './common';

export interface StrategyMetrics {
  train_score?: number;
  validation_score?: number;
  holdout_score?: number;
  backtest_score?: number;
  sharpe?: number;
  max_drawdown?: number;
  [key: string]: unknown;
}

export interface StrategyArtifact {
  id: string;
  name: string;
  version: string;
  status: AlphaLabStatus;
  market?: string;
  symbol_scope?: string;
  symbols?: string[];
  timeframe?: string;
  formula_tokens?: number[];
  formula_expression?: string;
  factor_schema_version?: string;
  feature_registry_hash?: string;
  operator_registry_hash?: string;
  signal_kernel_version?: string;
  min_exposure?: number;
  training_run_id?: string | null;
  data_snapshot_id?: string | null;
  training_range?: AlphaLabTimeRange | null;
  validation_ranges?: AlphaLabTimeRange[];
  holdout_range?: AlphaLabTimeRange | null;
  train_score?: number | null;
  validation_score?: number | null;
  holdout_score?: number | null;
  backtest_score?: number | null;
  metrics_json?: StrategyMetrics;
  robustness_json?: Record<string, unknown>;
  content_hash?: string;
  created_at?: string | null;
  promoted_at?: string | null;
  deprecated_at?: string | null;
  created_by?: string | null;
  lineage?: Record<string, unknown> | unknown[];
  realtime_references?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface StrategyImportRequest {
  name?: string;
  version?: string;
  [key: string]: unknown;
}

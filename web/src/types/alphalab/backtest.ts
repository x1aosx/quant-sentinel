import type { AlphaLabStatus } from './common';

export interface BacktestEquityPoint {
  timestamp: string;
  equity?: number;
  drawdown?: number;
  rolling_sharpe?: number;
}

export interface BacktestTrade {
  id?: string;
  symbol?: string;
  side?: string;
  direction?: string;
  entry_time?: string;
  exit_time?: string;
  entry_price?: number;
  exit_price?: number;
  quantity?: number;
  pnl?: number;
  return_pct?: number;
  holding_bars?: number;
  cost?: number;
}

export interface BacktestMetrics {
  total_return?: number;
  annualized_return?: number;
  annualized_volatility?: number;
  sharpe?: number;
  sortino?: number;
  max_drawdown?: number;
  calmar?: number;
  win_rate?: number;
  profit_loss_ratio?: number;
  trade_count?: number;
  turnover?: number;
  average_holding_bars?: number;
  exposure_mean?: number;
  exposure_max?: number;
  [key: string]: unknown;
}

export interface BacktestRun {
  id: string;
  name?: string;
  strategy_id: string;
  strategy_version?: string;
  status: AlphaLabStatus;
  data_snapshot_id?: string;
  config_json?: Record<string, unknown>;
  metrics_json?: BacktestMetrics;
  artifact_uri?: string | null;
  equity_curve?: BacktestEquityPoint[];
  drawdown_curve?: BacktestEquityPoint[];
  rolling_sharpe?: BacktestEquityPoint[];
  monthly_returns?: Record<string, number>;
  annual_returns?: Record<string, number>;
  per_symbol_metrics?: Record<string, BacktestMetrics>;
  trades?: BacktestTrade[];
  cost_breakdown?: Record<string, number>;
  robustness_json?: Record<string, unknown>;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  error_message?: string | null;
}

export interface BacktestCreateRequest {
  name?: string;
  strategy_id: string;
  data_snapshot_id?: string;
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  commission_pct?: number;
  slippage_pct?: number;
  min_exposure?: number;
}

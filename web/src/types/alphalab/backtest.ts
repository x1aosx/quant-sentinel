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
  entry_bar?: number;
  exit_bar?: number;
  entry_time?: string;
  exit_time?: string;
  entry_session?: string;
  exit_session?: string;
  entry_price?: number;
  exit_price?: number;
  quantity?: number;
  pnl?: number;
  return_pct?: number;
  holding_bars?: number;
  cost?: number;
}

export interface BacktestMetrics {
  initial_equity?: number;
  final_equity?: number;
  total_return?: number;
  annualized_return?: number;
  annualized_volatility?: number;
  sharpe?: number;
  sortino?: number;
  max_drawdown?: number;
  calmar?: number;
  win_rate?: number;
  profit_loss_ratio?: number;
  profit_factor?: number;
  average_win?: number;
  average_loss?: number;
  best_trade?: number;
  worst_trade?: number;
  win_count?: number;
  loss_count?: number;
  trade_count?: number;
  turnover?: number;
  average_turnover?: number;
  average_holding_bars?: number;
  exposure_mean?: number;
  exposure_max?: number;
  [key: string]: unknown;
}

export interface BacktestHoldingPeriod {
  count?: number;
  average_bars?: number;
  median_bars?: number;
  min_bars?: number;
  max_bars?: number;
  [key: string]: unknown;
}

export interface BacktestRobustness {
  status?: string;
  issues?: string[];
  monthly_returns?: Record<string, number>;
  annual_returns?: Record<string, number>;
  holding_period?: BacktestHoldingPeriod;
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
  initial_equity?: number;
  final_equity?: number;
  artifact_uri?: string | null;
  /** Merged equity / drawdown / rolling-Sharpe series, one point per bar. */
  equity_curve?: BacktestEquityPoint[];
  drawdown_curve?: BacktestEquityPoint[];
  rolling_sharpe?: BacktestEquityPoint[];
  /** Axis labels for `equity_curve`; `session` means real exchange sessions. */
  time_axis?: string[];
  time_axis_kind?: 'session' | 'bar_index';
  monthly_returns?: Record<string, number>;
  annual_returns?: Record<string, number>;
  per_symbol_metrics?: Record<string, BacktestMetrics>;
  trades?: BacktestTrade[];
  cost_breakdown?: Record<string, number>;
  execution_model?: Record<string, unknown>;
  cost_model?: Record<string, unknown>;
  robustness_json?: BacktestRobustness;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  error_message?: string | null;
}

export interface BacktestCreateRequest {
  name?: string;
  strategy_id: string;
  strategy_version?: string;
  data_snapshot_id?: string;
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  commission_pct?: number;
  slippage_pct?: number;
  min_exposure?: number;
}

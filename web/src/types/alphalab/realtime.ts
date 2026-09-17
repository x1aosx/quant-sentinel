import type { AlphaLabStatus, SignalDirection } from './common';

export interface RealtimeWatch {
  id: string;
  strategy_id: string;
  strategy_version?: string;
  strategy_name?: string;
  source: string;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  state?: AlphaLabStatus;
  last_closed_bar_ts?: string | null;
  last_factor?: number | null;
  last_position?: number | null;
  last_direction?: SignalDirection | null;
  last_strength?: number | null;
  updated_at?: string | null;
  last_error?: string | null;
  error?: string | null;
}

export interface RealtimeSignal {
  id: string;
  strategy_id: string;
  strategy_version?: string;
  strategy_name?: string;
  symbol: string;
  timeframe: string;
  bar_close_ts: string;
  direction: SignalDirection;
  position: number;
  strength: number;
  factor_value: number;
  created_at?: string | null;
  watch_id?: string;
}

export interface RealtimeWatchCreateRequest {
  source: string;
  symbol: string;
  timeframe: string;
  strategy_id: string;
  strategy_version?: string;
  enabled?: boolean;
}

export interface RealtimeEvaluateRequest {
  watch_id?: string;
  source?: string;
  symbol?: string;
  timeframe?: string;
  strategy_id?: string;
}

export interface RealtimeEvaluateResponse {
  evaluated?: number;
  generated?: number;
  signals?: RealtimeSignal[];
  errors?: Array<{ watch_id?: string; message: string }>;
  [key: string]: unknown;
}

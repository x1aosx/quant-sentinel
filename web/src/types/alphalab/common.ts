export type AlphaLabStatus =
  | 'DRAFT'
  | 'QUEUED'
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'
  | 'STOPPED'
  | 'PAUSED'
  | 'CANDIDATE'
  | 'VALIDATED'
  | 'PRODUCTION'
  | 'DEPRECATED'
  | 'REJECTED'
  | (string & {});

export type SignalDirection = 'LONG' | 'SHORT' | 'FLAT' | (string & {});

export interface AlphaLabList<T> {
  items: T[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface AlphaLabOverview {
  training_running?: number;
  running_training?: number;
  candidate_strategies?: number;
  validated_strategies?: number;
  production_strategies?: number;
  running_backtests?: number;
  realtime_watches?: number;
  recent_signal_changes?: number;
  resource_status?: string | Record<string, unknown>;
  updated_at?: string | null;
  training?: {
    total?: number;
    running?: number;
    recent?: Array<Record<string, unknown>>;
  };
  strategies?: {
    total?: number;
    production?: number;
    recent?: Array<Record<string, unknown>>;
  };
  backtests?: {
    total?: number;
    recent?: Array<Record<string, unknown>>;
  };
  realtime?: {
    watch_count?: number;
    watches?: Array<Record<string, unknown>>;
  };
  [key: string]: unknown;
}

export interface AlphaLabTimeRange {
  start?: string | null;
  end?: string | null;
  start_at?: string | null;
  end_at?: string | null;
}

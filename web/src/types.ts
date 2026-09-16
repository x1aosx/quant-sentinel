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
  title?: string;
  timeframe: string;
  bar_count: number;
  first_session: string;
  last_session: string;
  created_at: string;
  source?: 'yfinance' | 'akshare' | 'tradingview' | 'mt5' | 'local';
  source_provider?: string;
  exchange?: string;
  synced_at?: string;
  last_synced_at?: string;
}

export interface SyncDatasetResponse {
  dataset: DatasetSummary;
  id: string;
  symbol: string;
  title?: string;
  timeframe: string;
  bar_count: number;
  first_session: string;
  last_session: string;
  created_at: string;
  source: 'yfinance' | 'akshare' | 'tradingview' | 'mt5';
  source_provider: string;
  exchange?: string;
  inserted_count: number;
  updated_count: number;
  total_count: number;
  sync_status: 'updated' | 'unchanged';
  synced_at: string;
}

export interface SrLevel {
  zone_type: 'support' | 'resistance';
  zone_label?: string;
  center: number;
  low: number;
  high: number;
  distance_pct?: number;
  distance_atr?: number;
  width_atr?: number;
  width_pct?: number;
  edge_score?: number;
  n_events?: number;
  n_decided?: number;
  n_hold?: number;
  event_hold_rate?: number | null;
  touch_count?: number;
  volume_pct?: number;
  p_touch?: number | null;
  p_hold?: number | null;
  p_effective?: number | null;
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
    trend_detail?: {
      recent?: string;
      recent_score?: number;
      trading?: string;
      trading_score?: number;
      background?: string;
      background_score?: number;
    };
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
}

export interface UnifiedAnalysisResult extends SrAnalysisResult {
  price_action: PaAnalysisResult;
}

export interface StockAnalysisQuote {
  current_price: number | null;
  change_pct: number | null;
  last_session: string | null;
  timeframe: string | null;
}

export interface TimeframeAnalysisResult {
  symbol: string;
  timeframe: string;
  timestamp: string;
  trend: string;
  trend_score: number;
  phase: string;
  momentum_score: number;
  volatility_score: number;
  support_levels: number[];
  resistance_levels: number[];
  volume_state: string;
  price_structure: string;
  signals: string[];
  confidence: number;
  raw_result: Record<string, unknown>;
}

export interface MultiTimeframeAnalysisResult {
  symbol: string;
  timestamp: string;
  strategic_trend: string;
  strategic_score: number;
  intraday_state: string;
  confirmation_state: string;
  execution_state: string;
  alignment_score: number;
  conflict_score: number;
  bullish_score: number;
  bearish_score: number;
  summary_state: string;
  missing_timeframes: string[];
  metadata: {
    profile?: Record<string, string>;
    [key: string]: unknown;
  };
}

export interface StockAnalysisDecision {
  action: string;
  confidence: number;
  entry: number | null;
  stop: number | null;
  target: number | null;
  risk_reward: number | null;
  trigger_conditions: string[];
  invalid_conditions: string[];
  risk_flags: string[];
  reason_codes: string[];
}

export interface StockAnalysisAISummary {
  status: string;
  source: string;
  summary: string;
  cycle_explanation: string;
  scenarios: unknown;
  risks: string[];
  trigger_conditions: string[];
  invalid_conditions: string[];
  notification_text: string;
}

export interface StockAnalysisResponse {
  symbol: string;
  name: string;
  quote: StockAnalysisQuote;
  timeframes: Record<string, TimeframeAnalysisResult>;
  multi_timeframe: MultiTimeframeAnalysisResult;
  decision: StockAnalysisDecision;
  ai_summary: StockAnalysisAISummary | null;
  generated_at: string;
  from_cache: boolean;
  versions: Record<string, string>;
}

export interface AnalysisInstrumentSummary {
  dataset_id: string;
  symbol: string;
  title: string;
  timeframe: string;
  available_timeframes: string[];
  current_price: number | null;
  change_pct: number | null;
  touch_probability: number | null;
  hold_probability: number | null;
  historical_tests: number;
  trend: {
    label: string;
    detail?: string | null;
  };
  distance_pct: number | null;
  distance_atr: number | null;
  nearest_support: number | null;
  nearest_resistance: number | null;
  key_level: number | null;
  key_level_type: 'support' | 'resistance' | null;
  key_level_score: number | null;
  bars_used: number | null;
}

export type AnalysisChangeFilter = 'up' | 'down' | 'flat';

export interface AnalysisInstrumentSummariesParams {
  keyword?: string;
  timeframe?: string;
  trend?: string;
  change?: AnalysisChangeFilter;
  page?: number;
  page_size?: number;
  refresh?: boolean;
}

export interface AnalysisInstrumentSummariesResponse {
  items: AnalysisInstrumentSummary[];
  errors: Array<{
    dataset_id: string;
    symbol: string;
    detail: string;
  }>;
  count: number;
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  timeframe: string;
  facets: {
    timeframes: string[];
    trends: string[];
  };
  from_cache: boolean;
  generated_at: string;
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
  dataset_id?: string;
  persisted_at?: string;
  status: string;
  symbol: string;
  timeframe: string;
  created_at?: string;
  duration_ms?: number;
  action?: string;
  confidence?: number;
  snapshot: Record<string, any>;
  stage1_messages: Array<{ role: string; content: string }>;
  stage2_messages: Array<{ role: string; content: string }>;
  raw_prompt: Record<string, unknown>;
  stage1_response: string | Record<string, any>;
  stage2_response: string | Record<string, any>;
  stage1_response_text?: string;
  stage2_response_text?: string;
  stage1_diagnosis?: Record<string, any>;
  stage2_decision?: Record<string, any>;
  decision_trace?: Array<Record<string, any>>;
  future_trend?: Record<string, any>;
  next_cycle_prediction?: Record<string, any>;
  next_bar_prediction?: Record<string, any>;
  usage_total?: Record<string, number>;
  debug?: Record<string, any>;
  decision_tree_layout?: {
    nodes: Array<{
      id: string;
      label: string;
      question: string;
      answer: string;
      x: number;
      y: number;
      phase?: string;
      status?: string;
      reasoning?: string;
    }>;
    edges: Array<{ from: string; to: string }>;
  };
  exception?: Record<string, any> | null;
}

export interface AIRecordSummary {
  id: string;
  dataset_id?: string | null;
  symbol: string;
  timeframe: string;
  status: string;
  created_at?: string | null;
  duration_ms?: number | null;
  action?: string | null;
  confidence?: number | null;
  decision_action?: string | null;
}

export interface AIRecordListResponse {
  items: AIRecordSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface AIRecordDetailResponse {
  id?: string;
  record_id?: string;
  dataset_id?: string | null;
  created_at?: string | null;
  record: AIAnalysisRecord;
}

export interface AnalysisSettingsPayload {
  analysis_bar_count: number;
  decision_stance: 'conservative' | 'balanced' | 'aggressive' | 'extreme_aggressive';
  enable_next_bar_prediction: boolean;
  keep_analysis: boolean;
  incremental_max_new_bars: number;
  monitor_interval_seconds: number;
  concurrency: number;
}

export interface ProviderConfig {
  model: string;
  base_url: string;
  api_key: string;
  thinking: boolean;
  reasoning_effort: string;
  context_window: number;
  proxy_url: string;
  timeout_seconds: number;
  configured?: boolean;
  api_key_configured?: boolean;
  proxy_configured?: boolean;
}

export interface FeishuConfig {
  enabled: boolean;
  webhook_url: string;
  secret: string;
  app_id: string;
  app_secret: string;
  notify_on_order_only: boolean;
  confidence_threshold: number;
  configured?: boolean;
  webhook_configured?: boolean;
  secret_configured?: boolean;
  app_configured?: boolean;
}

export type IntelligenceSourceType = 'NEWS' | 'POLICY' | 'ANNOUNCEMENT';

export interface IntelligenceFeedConfig {
  id: string;
  url: string;
  source: string;
  source_type: IntelligenceSourceType;
  language: string;
  enabled: boolean;
}

export interface IntelligenceConfig {
  enabled: boolean;
  lookback_hours: number;
  feeds: IntelligenceFeedConfig[];
}

export interface MonitorTarget {
  symbol: string;
  timeframe: string;
  source: string;
  exchange?: string;
  dataset_id?: string;
  enabled: boolean;
  analysis?: Partial<AnalysisSettingsPayload>;
}

export type MonitorScheduleMode = 'always' | 'a_share' | 'custom';

export interface MonitorSchedule {
  mode: MonitorScheduleMode;
  timezone: string;
  enabled: boolean;
  weekdays: number[];
  custom_start: string;
  custom_end: string;
}

export interface SystemConfig {
  provider: ProviderConfig;
  analysis: AnalysisSettingsPayload;
  monitor_schedule: MonitorSchedule;
  feishu: FeishuConfig;
  intelligence: IntelligenceConfig;
  monitor_watchlist: MonitorTarget[];
}

export interface MonitorTargetStatus {
  target: MonitorTarget;
  status: string;
  last_session?: string | null;
  last_run_at?: string | null;
  last_check_at?: string | null;
  next_check_at?: string | null;
  last_status?: string | null;
  last_error?: Record<string, any> | null;
  last_record?: AIAnalysisRecord | null;
  run_count: number;
  success_count?: number;
  failure_count?: number;
  skip_count?: number;
  last_decision?: string | null;
  last_action?: string | null;
  last_confidence?: number | null;
  schedule_active_now?: boolean;
  new_bar_count: number;
}

export interface MonitorStatus {
  running: boolean;
  interval_seconds: number;
  auto_notify: boolean;
  last_cycle_at?: string | null;
  schedule?: MonitorSchedule;
  schedule_active_now?: boolean;
  schedule_label?: string;
  next_check_at?: string | null;
  current_time?: string | null;
  items: MonitorTargetStatus[];
}

export interface BatchAnalyzeResponse {
  status: string;
  summary: Record<string, number>;
  items: Array<{
    index: number;
    target: MonitorTarget;
    status: string;
    duration_ms: number;
    record?: AIAnalysisRecord;
    error?: Record<string, any> | null;
  }>;
}

export type SchedulerConcurrencyPolicy = 'ALLOW' | 'FORBID' | 'SERIAL' | 'REPLACE';
export type SchedulerMisfirePolicy = 'SKIP' | 'FIRE_ONCE' | 'CATCH_UP';
export type SchedulerRetryStrategy = 'fixed' | 'linear' | 'exponential';

export interface SchedulerRetryPolicy {
  max_attempts: number;
  strategy: SchedulerRetryStrategy;
  initial_delay_seconds: number;
  max_delay_seconds: number;
  multiplier: number;
  jitter: boolean;
  retry_on?: string[] | null;
  no_retry_on?: string[] | null;
}

export interface TaskDefinition {
  name: string;
  handler: string;
  description?: string | null;
  timeout_seconds: number;
  retry_policy?: SchedulerRetryPolicy | null;
  concurrency_policy: SchedulerConcurrencyPolicy;
  queue: string;
  priority: number;
  rate_limit_key?: string | null;
  planner?: string | null;
  enabled: boolean;
}

export interface CronTriggerDefinition {
  type: 'cron';
  minute: string;
  hour: string;
  day: string;
  month: string;
  day_of_week: string;
  timezone?: string;
}

export interface IntervalTriggerDefinition {
  type: 'interval';
  interval_seconds: number;
  start_at?: string | null;
  timezone?: string;
}

export interface DateTriggerDefinition {
  type: 'date';
  run_at: string;
  timezone?: string;
}

export interface FixedDelayTriggerDefinition {
  type: 'fixed_delay';
  delay_seconds: number;
  start_at?: string | null;
  timezone?: string;
}

export type SchedulerTrigger =
  | CronTriggerDefinition
  | IntervalTriggerDefinition
  | DateTriggerDefinition
  | FixedDelayTriggerDefinition;

export interface ScheduleDefinition {
  id: string;
  task_name: string;
  trigger: SchedulerTrigger;
  params: Record<string, unknown>;
  calendar?: string | null;
  timezone: string;
  misfire_policy: SchedulerMisfirePolicy;
  enabled: boolean;
  max_catch_up_runs: number;
  last_fire_at?: string | null;
  next_fire_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type SchedulerExecutionStatus =
  | 'PENDING'
  | 'WAITING'
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCESS'
  | 'FAILED'
  | 'RETRYING'
  | 'TIMEOUT'
  | 'CANCELLED'
  | 'SKIPPED';

export interface SchedulerTaskResult {
  success: boolean;
  data?: Record<string, unknown> | null;
  message?: string | null;
  metrics?: Record<string, unknown> | null;
}

export interface TaskExecution {
  id: string;
  task_name: string;
  scheduled_at: string;
  schedule_id?: string | null;
  parent_execution_id?: string | null;
  depends_on?: string[];
  queue: string;
  priority: number;
  status: SchedulerExecutionStatus;
  params: Record<string, unknown>;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  attempt: number;
  max_attempts: number;
  worker_id?: string | null;
  trace_id?: string;
  result?: SchedulerTaskResult | null;
  error_type?: string | null;
  error_message?: string | null;
  duration_ms?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WorkerSummary {
  worker_id: string;
  hostname?: string | null;
  pid?: number | null;
  queues?: string[];
  started_at?: string | null;
  last_heartbeat?: string | null;
  status?: string | null;
  running_tasks?: number;
  ttl_seconds?: number | null;
}

export type IntelligenceThemeCategory = 'hot' | 'emerging' | 'overcrowded';

export interface ThemeReference {
  id: string;
  code?: string | null;
  name: string;
}

export interface MarketEvent {
  id: string;
  event_type: string;
  title: string;
  summary: string;
  country?: string | null;
  importance: number;
  sentiment: number;
  confidence: number;
  novelty: number;
  impact_direction: string;
  impact_horizon: string;
  event_time: string;
  first_publish_time: string;
  last_update_time: string;
  heat_score: number;
  source_count: number;
  status: string;
  affected_themes?: string[];
  affected_industries?: string[];
  affected_stocks?: string[];
}

export interface ThemeSummary {
  id: string;
  code?: string | null;
  name: string;
  description?: string | null;
  status?: string | null;
  category?: IntelligenceThemeCategory | string | null;
  current_heat_score: number;
  forward_heat_score: number;
  crowding_score: number;
  sentiment_score?: number | null;
  policy_score?: number | null;
  capital_score?: number | null;
  confidence?: number | null;
  event_count?: number;
  stock_count?: number;
  updated_at?: string | null;
}

export interface IntelligenceBriefSection {
  title: string;
  content: string | string[];
}

export interface IntelligenceBrief {
  id?: string;
  brief_date?: string | null;
  trade_date?: string | null;
  title?: string | null;
  summary?: string | null;
  content?: string | null;
  generated_at?: string | null;
  sections?: IntelligenceBriefSection[] | null;
  market_environment?: string | null;
  important_policies?: string[];
  global_events?: string[];
  current_hotspots?: string[];
  potential_hotspots?: string[];
  focus_stocks?: string[];
  holding_impacts?: string[];
  risk_events?: string[];
}

export interface IntelligenceOverview {
  information_count: number;
  event_count: number;
  theme_count: number;
  source_count: number;
  generated_at: string;
  hot_events: MarketEvent[];
  themes: ThemeSummary[];
  brief: IntelligenceBrief | null;
}

export interface IntelligenceRunRequest {
  process_only?: boolean;
}

export interface IntelligenceRunResult {
  collected: number;
  deduplicated: number;
  event_count: number;
  theme_count: number;
  generated_at: string;
}

export interface MarketEventListResponse {
  items: MarketEvent[];
  count: number;
}

export interface ThemeListResponse {
  items: ThemeSummary[];
  count: number;
}

export interface MorningBriefResponse {
  brief: IntelligenceBrief | null;
}

export type DiscoveryCandidateState =
  | 'DISCOVERED'
  | 'WATCH'
  | 'FOCUS'
  | 'DEEP_ANALYSIS'
  | 'SIGNAL_READY'
  | 'COOLDOWN'
  | 'REMOVE';

export type DiscoveryScoreMap = Record<string, number | null | undefined>;

export interface DiscoveryScoreContribution {
  key?: string;
  factor?: string;
  name?: string;
  label?: string;
  score?: number | null;
  weight?: number | null;
  contribution?: number | null;
}

export interface DiscoveryCandidate {
  stock_id: string;
  stock_code?: string | null;
  code?: string | null;
  symbol?: string | null;
  stock_name?: string | null;
  name?: string | null;
  discovery_score: number;
  rank: number;
  state: DiscoveryCandidateState | string;
  market_regime?: string | null;
  theme_ids?: string[];
  theme_names?: string[];
  themes?: Array<string | ThemeReference>;
  scores?: DiscoveryScoreMap | null;
  score_contributions?: DiscoveryScoreContribution[] | DiscoveryScoreMap | null;
  forward_heat_score?: number | null;
  forward_theme_score?: number | null;
  crowding_score?: number | null;
  risk_score?: number | null;
  reasons?: string[];
  risks?: string[];
  confidence?: number | null;
  model_version: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DiscoveryCandidateListResponse {
  trade_date: string;
  items: DiscoveryCandidate[];
  count: number;
}

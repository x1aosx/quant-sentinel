import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  BrainCircuit,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Gauge,
  Layers,
  ListChecks,
  Loader2,
  Play,
  RefreshCw,
  ScanSearch,
  Search,
  TrendingUp,
} from 'lucide-react';
import { api } from '../api/client';
import { KlineChart } from '../components/KlineChart';
import type {
  AnalysisChangeFilter,
  AnalysisInstrumentSummariesParams,
  AnalysisInstrumentSummary,
  SrLevel,
} from '../types';
import {
  DEFAULT_ANALYSIS_TIMEFRAME,
  datasetForTimeframe,
  findStockGroup,
  formatTimeframeLabel,
  groupDatasetsByStock,
  preferredDataset,
} from '../utils/datasetDisplay';

const DIRECTION_LABELS: Record<string, string> = {
  bullish: '偏多',
  bearish: '偏空',
  neutral: '中性',
};

const CYCLE_LABELS: Record<string, string> = {
  breakout: '突破',
  breakdown: '跌破',
  breakout_attempt: '突破尝试',
  trend_pullback: '上升回踩',
  trend_rebound: '下降反弹',
  trending: '趋势运行',
  trading_range: '区间震荡',
  range_boundary: '区间边界',
};

const SUMMARY_PAGE_SIZES = [20, 50, 100, 200] as const;

const CHANGE_LABELS: Record<AnalysisChangeFilter, string> = {
  up: '上涨',
  down: '下跌',
  flat: '持平',
};

interface AnalysisPayload {
  symbol: string;
  timeframe: string;
  lookback?: number;
  n_zones?: number;
  direction: 'both' | 'long' | 'short';
  risk_fraction?: number;
  min_rr?: number;
  stance: 'conservative' | 'balanced' | 'aggressive';
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

function fmtSignedPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

function fmtProbability(value: number | null | undefined, missing = '--'): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return missing;
  return `${(value * 100).toFixed(1)}%`;
}

function fmtDirectionScore(direction?: string, score?: number): string {
  const label = direction ? DIRECTION_LABELS[direction] ?? direction : '--';
  if (score === undefined || !Number.isFinite(score)) return label;
  return `${label} ${score > 0 ? '+' : ''}${score}`;
}

function fmtGeneratedAt(value?: string): string {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function parseOptionalInt(value: string, label: string): number | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || Math.trunc(parsed) !== parsed || parsed <= 0) {
    throw new Error(`${label} 必须是正整数`);
  }
  return Math.trunc(parsed);
}

function parseOptionalFloat(value: string, label: string, min: number, max: number): number | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
    throw new Error(`${label} 必须在 ${min} 到 ${max} 之间`);
  }
  return parsed;
}

function changePct(candles: Array<{ close: number }>): number | null {
  if (candles.length < 2) return null;
  const previous = candles[candles.length - 2].close;
  const current = candles[candles.length - 1].close;
  if (!previous) return null;
  return ((current - previous) / previous) * 100;
}

function directionClass(value?: string): string {
  if (value === 'bullish') return 'summary-value market-up';
  if (value === 'bearish') return 'summary-value market-down';
  return 'summary-value market-flat';
}

function levelLabel(level: SrLevel | null | undefined): string {
  if (!level) return '--';
  return `${level.zone_type === 'support' ? '支撑' : '压力'} ${fmtNum(level.center, 4)}`;
}

export function MarketAnalysisPage() {
  const queryClient = useQueryClient();
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [selectedTimeframe, setSelectedTimeframe] = useState(
    DEFAULT_ANALYSIS_TIMEFRAME,
  );
  const [lookback, setLookback] = useState('250');
  const [nZones, setNZones] = useState('6');
  const [direction, setDirection] = useState<'both' | 'long' | 'short'>('both');
  const [riskFraction, setRiskFraction] = useState('0.01');
  const [minRr, setMinRr] = useState('1.5');
  const [stance, setStance] = useState<'conservative' | 'balanced' | 'aggressive'>('balanced');
  const [pageError, setPageError] = useState('');
  const [summaryKeyword, setSummaryKeyword] = useState('');
  const [summaryTimeframe, setSummaryTimeframe] = useState(
    DEFAULT_ANALYSIS_TIMEFRAME,
  );
  const [summaryTrend, setSummaryTrend] = useState('');
  const [summaryChange, setSummaryChange] = useState<AnalysisChangeFilter | ''>('');
  const [summaryPage, setSummaryPage] = useState(1);
  const [summaryPageSize, setSummaryPageSize] = useState(20);
  const detailRef = useRef<HTMLDivElement | null>(null);

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const summaryParams: AnalysisInstrumentSummariesParams = {
    page: summaryPage,
    page_size: summaryPageSize,
    ...(summaryKeyword.trim() ? { keyword: summaryKeyword.trim() } : {}),
    ...(summaryTimeframe ? { timeframe: summaryTimeframe } : {}),
    ...(summaryTrend ? { trend: summaryTrend } : {}),
    ...(summaryChange ? { change: summaryChange } : {}),
  };
  const summariesQuery = useQuery({
    queryKey: ['instrument-summaries', summaryParams],
    queryFn: () => api.getInstrumentSummaries(summaryParams),
  });
  const refreshSummaryMutation = useMutation({
    mutationFn: (params: AnalysisInstrumentSummariesParams) =>
      api.getInstrumentSummaries({ ...params, refresh: true }),
    onSuccess: (data, params) => {
      queryClient.setQueryData(['instrument-summaries', params], data);
    },
  });
  const datasets = datasetsQuery.data?.items ?? [];
  const stockGroups = useMemo(() => groupDatasetsByStock(datasets), [datasets]);
  const selectedGroup = findStockGroup(stockGroups, selectedSymbol);
  const selectedDataset = datasetForTimeframe(selectedGroup, selectedTimeframe);
  const summaryData = summariesQuery.data;
  const summaryTotalPages = summaryData?.total_pages ?? 0;
  const summaryPageStart =
    summaryData && summaryData.count > 0
      ? (summaryData.page - 1) * summaryData.page_size + 1
      : 0;
  const summaryPageEnd =
    summaryData && summaryData.count > 0
      ? summaryPageStart + summaryData.items.length - 1
      : 0;
  const summaryCacheLabel = summaryData
    ? `${summaryData.from_cache ? '缓存结果' : '实时计算'} · ${fmtGeneratedAt(summaryData.generated_at)}`
    : '';
  const hasSummaryFilters = Boolean(
    summaryKeyword.trim() ||
      summaryTimeframe !== DEFAULT_ANALYSIS_TIMEFRAME ||
      summaryTrend ||
      summaryChange,
  );

  useEffect(() => {
    if (!stockGroups.length) return;
    const group = findStockGroup(stockGroups, selectedSymbol) ?? stockGroups[0];
    if (group.symbol !== selectedSymbol) setSelectedSymbol(group.symbol);
    if (!datasetForTimeframe(group, selectedTimeframe)) {
      const dataset = preferredDataset(group, selectedTimeframe);
      if (dataset) setSelectedTimeframe(dataset.timeframe);
    }
  }, [selectedSymbol, selectedTimeframe, stockGroups]);

  useEffect(() => {
    if (!summaryData) return;
    const lastPage = Math.max(summaryData.total_pages, 1);
    if (summaryPage > lastPage) setSummaryPage(lastPage);
  }, [summaryData, summaryPage]);

  const analysis = useMutation({
    mutationFn: (payload: AnalysisPayload) => api.analyzeSupportResistance(payload),
  });

  const activeSymbol = analysis.variables?.symbol ?? selectedSymbol;
  const result = analysis.data;
  const riskReward = result?.summary.risk_reward;
  const context = result?.price_action.market_context;
  const features = result?.price_action.features;
  const decision = result?.price_action.decision;
  const resultChange = result ? changePct(result.candles) : null;
  const resultChangeClass =
    resultChange === null ? 'market-flat' : resultChange >= 0 ? 'market-up' : 'market-down';

  const buildPayload = (symbol: string, timeframe: string): AnalysisPayload => {
    const parsedLookback = parseOptionalInt(lookback, 'lookback');
    const parsedNZones = parseOptionalInt(nZones, '区间数量');
    const parsedRiskFraction = parseOptionalFloat(riskFraction, 'risk_fraction', 0.0001, 0.2);
    const parsedMinRr = parseOptionalFloat(minRr, 'min_rr', 0, 20);
    return {
      symbol,
      timeframe,
      ...(parsedLookback !== undefined ? { lookback: parsedLookback } : {}),
      ...(parsedNZones !== undefined ? { n_zones: parsedNZones } : {}),
      direction,
      ...(parsedRiskFraction !== undefined ? { risk_fraction: parsedRiskFraction } : {}),
      ...(parsedMinRr !== undefined ? { min_rr: parsedMinRr } : {}),
      stance,
    };
  };

  const runAnalysis = (
    symbol: string,
    timeframe: string,
    scrollToDetail = false,
  ) => {
    setPageError('');
    const group = findStockGroup(stockGroups, symbol);
    const dataset = datasetForTimeframe(group, timeframe);
    if (!dataset) {
      setPageError('请选择已有行情的股票与周期（可先到数据中心导入）。');
      return;
    }
    try {
      const payload = buildPayload(symbol, timeframe);
      setSelectedSymbol(symbol);
      setSelectedTimeframe(timeframe);
      if (scrollToDetail) {
        window.requestAnimationFrame(() => {
          detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      }
      analysis.reset();
      analysis.mutate(payload);
    } catch (error) {
      setPageError(error instanceof Error ? error.message : String(error));
    }
  };

  const handleSummarySelect = (item: AnalysisInstrumentSummary) => {
    runAnalysis(item.symbol, item.timeframe, true);
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>支撑阻力与价格行为</h1>
          <div className="muted">
            用一个分析入口查看全部股票概览，并结合关键价位、市场结构与行为决策查看完整信息。
          </div>
        </div>
        <span className="badge badge-info">simulation only</span>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Layers size={16} />
            全部股票汇总
            <span className="tag">{summaryData?.count ?? 0} 只股票</span>
          </span>
          <span className="section-title-actions">
            {summaryCacheLabel ? (
              <span className="summary-cache-meta" title={summaryCacheLabel}>
                {summaryCacheLabel}
              </span>
            ) : null}
            <button
              className="button"
              onClick={() => refreshSummaryMutation.mutate(summaryParams)}
              disabled={summariesQuery.isFetching || refreshSummaryMutation.isPending}
            >
              <RefreshCw size={14} />
              {refreshSummaryMutation.isPending ? '刷新中' : '刷新汇总'}
            </button>
          </span>
        </div>

        <div className="summary-toolbar">
          <label className="field summary-search">
            <span>关键词</span>
            <span className="summary-search-control">
              <Search size={14} aria-hidden="true" />
              <input
                type="search"
                value={summaryKeyword}
                onChange={(event) => {
                  setSummaryKeyword(event.target.value);
                  setSummaryPage(1);
                }}
                placeholder="搜索 symbol 或名称"
                aria-label="搜索汇总股票"
              />
            </span>
          </label>
          <label className="field summary-filter-field">
            <span>周期</span>
            <select
              value={summaryTimeframe}
              onChange={(event) => {
                setSummaryTimeframe(event.target.value);
                setSummaryPage(1);
              }}
            >
              {summaryTimeframe &&
              !(summaryData?.facets.timeframes ?? []).includes(summaryTimeframe) ? (
                <option value={summaryTimeframe}>{summaryTimeframe}</option>
              ) : null}
              {(summaryData?.facets.timeframes ?? []).map((timeframe) => (
                <option key={timeframe} value={timeframe}>
                  {timeframe}
                </option>
              ))}
            </select>
          </label>
          <label className="field summary-filter-field">
            <span>趋势</span>
            <select
              value={summaryTrend}
              onChange={(event) => {
                setSummaryTrend(event.target.value);
                setSummaryPage(1);
              }}
            >
              <option value="">全部趋势</option>
              {summaryTrend && !(summaryData?.facets.trends ?? []).includes(summaryTrend) ? (
                <option value={summaryTrend}>{summaryTrend}</option>
              ) : null}
              {(summaryData?.facets.trends ?? []).map((trend) => (
                <option key={trend} value={trend}>
                  {trend}
                </option>
              ))}
            </select>
          </label>
          <label className="field summary-filter-field">
            <span>涨跌</span>
            <select
              value={summaryChange}
              onChange={(event) => {
                setSummaryChange(event.target.value as AnalysisChangeFilter | '');
                setSummaryPage(1);
              }}
            >
              <option value="">全部涨跌</option>
              {(Object.entries(CHANGE_LABELS) as Array<[AnalysisChangeFilter, string]>).map(
                ([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ),
              )}
            </select>
          </label>
        </div>

        {refreshSummaryMutation.isError ? (
          <div className="empty" style={{ marginBottom: 14 }}>
            刷新汇总失败：{(refreshSummaryMutation.error as Error).message}
          </div>
        ) : null}

        {summariesQuery.isLoading ? (
          <div className="empty">正在加载全部股票汇总...</div>
        ) : summariesQuery.isError ? (
          <div className="empty">
            <span>
              <AlertTriangle size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
              汇总加载失败：{(summariesQuery.error as Error).message}
            </span>
          </div>
        ) : summaryData?.items.length ? (
          <>
            <div className="table-wrap">
              <table className="table summary-table">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>中文名称</th>
                    <th>现价</th>
                    <th>涨跌%</th>
                    <th>触及概率</th>
                    <th>守住概率</th>
                    <th>历史被测试</th>
                    <th>趋势</th>
                    <th>距离</th>
                    <th>最近支撑</th>
                    <th>最近压力</th>
                    <th>关键位</th>
                  </tr>
                </thead>
                <tbody>
                  {summaryData.items.map((item) => {
                    const selected = item.symbol === activeSymbol;
                    return (
                      <tr
                        key={item.symbol}
                        className={`summary-row${selected ? ' selected' : ''}`}
                        tabIndex={0}
                        role="button"
                        aria-label={`查看 ${item.title} 完整分析`}
                        onClick={() => handleSummarySelect(item)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            handleSummarySelect(item);
                          }
                        }}
                      >
                        <td>
                          <div className="summary-symbol">{item.symbol}</div>
                          <div className="summary-subtext">
                            {formatTimeframeLabel(item.timeframe)} · {item.bars_used ?? 0} 根
                            {item.available_timeframes.length > 1
                              ? ` · ${item.available_timeframes.length} 个周期`
                              : ''}
                          </div>
                        </td>
                        <td>{item.title}</td>
                        <td className="summary-value">{fmtNum(item.current_price, 4)}</td>
                        <td
                          className={
                            item.change_pct === null || item.change_pct === undefined
                              ? 'summary-value market-flat'
                              : `summary-value market-${item.change_pct >= 0 ? 'up' : 'down'}`
                          }
                        >
                          {fmtSignedPct(item.change_pct)}
                        </td>
                        <td className="summary-value">
                          {fmtProbability(item.touch_probability, '未标定')}
                        </td>
                        <td className="summary-value">{fmtProbability(item.hold_probability)}</td>
                        <td className="summary-value">{item.historical_tests}</td>
                        <td>
                          <span className="tag">{item.trend.label}</span>
                        </td>
                        <td>
                          <div className="summary-value">{fmtSignedPct(item.distance_pct)}</div>
                          <div className="summary-subtext">
                            {item.distance_atr === null ? '--' : `${fmtNum(item.distance_atr)} ATR`}
                          </div>
                        </td>
                        <td className="summary-value support-value">
                          {fmtNum(item.nearest_support, 4)}
                        </td>
                        <td className="summary-value resistance-value">
                          {fmtNum(item.nearest_resistance, 4)}
                        </td>
                        <td>
                          <div className="summary-value">{fmtNum(item.key_level, 4)}</div>
                          <div className="summary-subtext">
                            {item.key_level_type
                              ? `${item.key_level_type === 'support' ? '支撑' : '压力'} · ${fmtNum(item.key_level_score)}`
                              : '--'}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="summary-pagination">
              <span className="muted">
                第 {summaryPageStart}-{summaryPageEnd} 条，共 {summaryData.count} 条
              </span>
              <div className="summary-page-actions">
                <button
                  className="button"
                  type="button"
                  onClick={() => setSummaryPage((current) => Math.max(current - 1, 1))}
                  disabled={
                    summariesQuery.isFetching ||
                    refreshSummaryMutation.isPending ||
                    summaryPage <= 1
                  }
                >
                  <ChevronLeft size={14} />
                  上一页
                </button>
                <span className="summary-page-indicator">
                  第 {Math.max(summaryData.page, 1)} / {Math.max(summaryTotalPages, 1)} 页
                </span>
                <button
                  className="button"
                  type="button"
                  onClick={() =>
                    setSummaryPage((current) =>
                      Math.min(current + 1, Math.max(summaryTotalPages, 1)),
                    )
                  }
                  disabled={
                    summariesQuery.isFetching ||
                    refreshSummaryMutation.isPending ||
                    summaryTotalPages === 0 ||
                    summaryPage >= summaryTotalPages
                  }
                >
                  下一页
                  <ChevronRight size={14} />
                </button>
              </div>
              <label className="summary-page-size">
                每页
                <select
                  value={summaryPageSize}
                  onChange={(event) => {
                    setSummaryPageSize(Number(event.target.value));
                    setSummaryPage(1);
                  }}
                  aria-label="每页条数"
                >
                  {SUMMARY_PAGE_SIZES.map((pageSize) => (
                    <option key={pageSize} value={pageSize}>
                      {pageSize}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {summaryData.errors.length ? (
              <div className="empty" style={{ marginTop: 14 }}>
                {summaryData.errors.length} 只股票未能生成汇总：
                {summaryData.errors.map((item) => `${item.symbol} ${item.detail}`).join('；')}
              </div>
            ) : null}
            <p className="muted" style={{ marginBottom: 0 }}>
              触及概率在模型未标定时显示“未标定”；守住概率优先使用标定值，缺失时回退为历史事件守住率。
            </p>
          </>
        ) : summaryData ? (
          <div className="empty">
            {hasSummaryFilters
              ? '没有符合筛选条件的股票。'
              : '暂无股票数据，请先到数据中心导入行情。'}
          </div>
        ) : (
          <div className="empty">暂无汇总结果。</div>
        )}
      </div>

      <div ref={detailRef} className="analysis-detail-anchor">
        <div className="panel">
          <div className="section-title">
            <span className="row">
              <BrainCircuit size={16} />
              综合分析
              {selectedDataset ? (
                <span className="tag">
                  {selectedDataset.symbol} · {formatTimeframeLabel(selectedDataset.timeframe)}
                </span>
              ) : null}
            </span>
            <span className="tag">点击汇总行可直接载入</span>
          </div>
          <div className="form-grid analysis-form-grid">
            <label className="field">
              <span>股票</span>
              <select
                value={selectedGroup?.symbol ?? ''}
                onChange={(event) => {
                  const group = findStockGroup(stockGroups, event.target.value);
                  const dataset = preferredDataset(group, selectedTimeframe);
                  if (!dataset) return;
                  setSelectedSymbol(group?.symbol ?? '');
                  setSelectedTimeframe(dataset.timeframe);
                }}
              >
                {stockGroups.length === 0 ? <option value="">暂无股票</option> : null}
                {stockGroups.map((group) => (
                  <option key={group.symbol} value={group.symbol}>
                    {group.title || group.symbol} · {group.symbol}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>周期</span>
              <select
                value={selectedTimeframe}
                onChange={(event) => setSelectedTimeframe(event.target.value)}
                disabled={!selectedGroup}
              >
                {(selectedGroup?.datasets ?? []).map((dataset) => (
                  <option key={dataset.id} value={dataset.timeframe}>
                    {formatTimeframeLabel(dataset.timeframe)} · {dataset.bar_count} 根
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Lookback</span>
              <input
                value={lookback}
                onChange={(event) => setLookback(event.target.value)}
                placeholder="如 250，可留空"
              />
            </label>
            <label className="field">
              <span>区间数量</span>
              <input
                value={nZones}
                onChange={(event) => setNZones(event.target.value)}
                placeholder="如 6，可留空"
              />
            </label>
            <label className="field">
              <span>支撑阻力方向</span>
              <select
                value={direction}
                onChange={(event) =>
                  setDirection(event.target.value as 'both' | 'long' | 'short')
                }
              >
                <option value="both">双向 both</option>
                <option value="long">仅做多 long</option>
                <option value="short">仅做空 short</option>
              </select>
            </label>
            <label className="field">
              <span>Risk fraction</span>
              <input
                value={riskFraction}
                onChange={(event) => setRiskFraction(event.target.value)}
                placeholder="如 0.01"
              />
            </label>
            <label className="field">
              <span>最小 RR</span>
              <input
                value={minRr}
                onChange={(event) => setMinRr(event.target.value)}
                placeholder="如 1.5"
              />
            </label>
            <label className="field">
              <span>分析倾向</span>
              <select
                value={stance}
                onChange={(event) =>
                  setStance(event.target.value as 'conservative' | 'balanced' | 'aggressive')
                }
              >
                <option value="conservative">保守 conservative</option>
                <option value="balanced">均衡 balanced</option>
                <option value="aggressive">积极 aggressive</option>
              </select>
            </label>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button
              className="button button-primary"
              onClick={() => runAnalysis(selectedSymbol, selectedTimeframe)}
              disabled={analysis.isPending || !selectedDataset}
            >
              {analysis.isPending ? <Loader2 size={14} /> : <Play size={14} />}
              {analysis.isPending ? '分析中...' : '开始综合分析'}
            </button>
            <span className="muted">一次综合分析请求会同时返回支撑阻力与价格行为结果。</span>
          </div>
          {datasetsQuery.isError ? (
            <div className="empty" style={{ marginTop: 14 }}>
              股票数据列表加载失败：{(datasetsQuery.error as Error).message}
            </div>
          ) : null}
          {pageError ? (
            <div className="empty" style={{ marginTop: 14 }}>
              {pageError}
            </div>
          ) : null}
          {analysis.isError ? (
            <div className="empty" style={{ marginTop: 14 }}>
              综合分析失败：{(analysis.error as Error).message}
            </div>
          ) : null}
        </div>
      </div>

      {!result && !analysis.isPending && !pageError && !analysis.isError ? (
        <div className="empty">
          <span>
            <ScanSearch size={18} style={{ display: 'block', margin: '0 auto 8px' }} />
            选择股票与周期并开始分析，或点击上方汇总表任意股票查看完整信息。
          </span>
        </div>
      ) : null}

      {analysis.isPending ? <div className="empty">正在生成支撑阻力与价格行为结果...</div> : null}

      {result && context && features && decision ? (
        <>
          <div className="grid grid-4">
            <div className="panel stat">
              <div>
                <div className="stat-label">现价</div>
                <div className="stat-value">{fmtNum(result.current_price, 4)}</div>
              </div>
              <span className="stat-icon"><CircleDollarSign size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">最近涨跌</div>
                <div className={`stat-value ${resultChangeClass}`}>
                  {fmtSignedPct(resultChange)}
                </div>
              </div>
              <span className="stat-icon info"><TrendingUp size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">ATR</div>
                <div className="stat-value">{fmtNum(result.atr, 4)}</div>
                <div className="muted">{fmtNum(result.atr_pct)}%</div>
              </div>
              <span className="stat-icon warn"><Gauge size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">行为决策</div>
                <div className="stat-value">{decision.action}</div>
                <div className="muted">置信度 {fmtNum(decision.confidence, 0)}%</div>
              </div>
              <span className="stat-icon"><ListChecks size={18} /></span>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">
              <span>综合概览</span>
              <span className="row">
                <span className="badge badge-info">{result.symbol}</span>
                <span className="tag">{result.timeframe}</span>
                <span className="tag">{result.bars_used} 根K线</span>
              </span>
            </div>
            {result.summary.headline ? (
              <div className="feature-item">
                <div className="label">关键价位结论</div>
                <div className="value">{result.summary.headline}</div>
              </div>
            ) : null}
            <div className="metric-list" style={{ marginTop: 14 }}>
              <div>
                <div className="label">支撑阻力趋势</div>
                <div className="value">{result.trend.label}</div>
              </div>
              <div>
                <div className="label">价格行为方向</div>
                <div className={directionClass(context.direction)}>
                  {DIRECTION_LABELS[context.direction] ?? context.direction}
                </div>
              </div>
              <div>
                <div className="label">市场阶段</div>
                <div className="value">
                  {context.cycle_position
                    ? CYCLE_LABELS[context.cycle_position] ?? context.cycle_position
                    : '--'}
                </div>
              </div>
              <div>
                <div className="label">风险回报</div>
                <div className="value">{fmtNum(riskReward?.risk_reward_ratio)}</div>
              </div>
              <div>
                <div className="label">最近关键位</div>
                <div className="value">{levelLabel(result.summary.nearest)}</div>
              </div>
              <div>
                <div className="label">最佳评分位</div>
                <div className="value">{levelLabel(result.summary.best)}</div>
              </div>
            </div>
            {result.summary.caveat ? (
              <p className="muted" style={{ marginBottom: 0 }}>
                提示：{result.summary.caveat}
              </p>
            ) : null}
          </div>

          <div className="grid grid-2">
            <div className="panel">
              <div className="section-title">
                <span>市场结构</span>
                <span className="tag">{result.price_action.timeframe}</span>
              </div>
              <div className="row">
                {features.swing_structure ? <span className="tag">{features.swing_structure}</span> : null}
                {features.breakout_quality ? <span className="tag">{features.breakout_quality}</span> : null}
                {(features.patterns ?? []).map((pattern) => (
                  <span key={pattern} className="tag">{pattern}</span>
                ))}
              </div>
              <div className="feature-list" style={{ marginTop: 14 }}>
                <div className="feature-item">
                  <div className="label">价格位置</div>
                  <div className="value">
                    {context.price_position === undefined
                      ? '--'
                      : `${(context.price_position * 100).toFixed(1)}%`}
                  </div>
                </div>
                <div className="feature-item">
                  <div className="label">观察区间</div>
                  <div className="value">
                    {fmtNum(context.range_low, 4)} ~ {fmtNum(context.range_high, 4)}
                  </div>
                </div>
                <div className="feature-item">
                  <div className="label">趋势细节</div>
                  <div className="value stack" style={{ gap: 2 }}>
                    <div>短线：{fmtDirectionScore(context.trend_detail?.recent, context.trend_detail?.recent_score)}</div>
                    <div>交易：{fmtDirectionScore(context.trend_detail?.trading, context.trend_detail?.trading_score)}</div>
                    <div>背景：{fmtDirectionScore(context.trend_detail?.background, context.trend_detail?.background_score)}</div>
                  </div>
                </div>
                <div className="feature-item">
                  <div className="label">结构事件</div>
                  <div className="value">
                    突破 {features.breakout_events?.length ?? 0} · 摆动 {features.swings?.length ?? 0}
                  </div>
                </div>
              </div>
            </div>

            <div className="panel">
              <div className="section-title">
                <span>行为决策</span>
                <span className="tag">{result.price_action.bars_used} 根K线</span>
              </div>
              <div className="row">
                <span
                  className={`badge ${
                    decision.action === 'LONG'
                      ? 'badge-ok'
                      : decision.action === 'SHORT'
                        ? 'badge-danger'
                        : 'badge-neutral'
                  }`}
                >
                  {decision.action === 'LONG'
                    ? '做多观察'
                    : decision.action === 'SHORT'
                      ? '做空观察'
                      : '观望'}
                </span>
                <span className="tag">置信度 {fmtNum(decision.confidence, 0)}%</span>
                {decision.risk_fraction ? (
                  <span className="tag">单笔风险 {(decision.risk_fraction * 100).toFixed(2)}%</span>
                ) : null}
              </div>
              <div className="metric-list" style={{ marginTop: 14 }}>
                <div>
                  <div className="label">Entry</div>
                  <div className="value">{fmtNum(decision.entry, 4)}</div>
                </div>
                <div>
                  <div className="label">Stop</div>
                  <div className="value">{fmtNum(decision.stop, 4)}</div>
                </div>
                <div>
                  <div className="label">Target</div>
                  <div className="value">{fmtNum(decision.target, 4)}</div>
                </div>
                <div>
                  <div className="label">RR</div>
                  <div className="value">{fmtNum(decision.rr)}</div>
                </div>
              </div>
              {decision.reasoning ? (
                <div className="feature-item" style={{ marginTop: 14 }}>
                  <div className="label">判断依据</div>
                  <div className="value">{decision.reasoning}</div>
                </div>
              ) : null}
              {decision.invalidation ? (
                <div className="feature-item" style={{ marginTop: 8 }}>
                  <div className="label">失效条件</div>
                  <div className="value">{decision.invalidation}</div>
                </div>
              ) : null}
            </div>
          </div>

          <div className="panel">
            <div className="section-title">
              <span>完整价位区间</span>
              <span className="tag">{result.levels.length} 个</span>
            </div>
            {result.levels.length === 0 ? (
              <div className="empty">当前参数下未识别出价位区间。</div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>类型</th>
                      <th>中心价</th>
                      <th>区间</th>
                      <th>距离</th>
                      <th>触及概率</th>
                      <th>守住概率</th>
                      <th>历史被测试</th>
                      <th>守住 / 已决</th>
                      <th>成交量</th>
                      <th>边界评分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.levels.map((level, index) => (
                      <tr key={`${level.zone_type}-${level.center}-${index}`}>
                        <td>
                          <span className={`zone-chip ${level.zone_type}`}>
                            {level.zone_type === 'support' ? '支撑' : '压力'}
                          </span>
                        </td>
                        <td>{fmtNum(level.center, 4)}</td>
                        <td>
                          {fmtNum(level.low, 4)} ~ {fmtNum(level.high, 4)}
                        </td>
                        <td>
                          <div>{fmtSignedPct(level.distance_pct)}</div>
                          <div className="summary-subtext">{fmtNum(level.distance_atr)} ATR</div>
                        </td>
                        <td>{fmtProbability(level.p_touch, '未标定')}</td>
                        <td>
                          {fmtProbability(level.p_hold ?? level.event_hold_rate)}
                        </td>
                        <td>{level.n_events ?? level.touch_count ?? 0}</td>
                        <td>
                          {level.n_hold ?? 0} / {level.n_decided ?? 0}
                        </td>
                        <td>{fmtNum(level.volume_pct)}%</td>
                        <td>{fmtNum(level.edge_score)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="panel">
            <div className="section-title">
              <span>K线与价位区间</span>
              <span className="row">
                <span className="tag">{result.symbol}</span>
                <span className="tag">最近 150 根</span>
              </span>
            </div>
            <KlineChart candles={result.candles.slice(-150)} levels={result.levels} />
            <p className="muted" style={{ marginBottom: 0 }}>
              支撑阻力区间已合并显示在这一张图上；以上结果仅用于研究与演示，不构成投资建议。
            </p>
          </div>
        </>
      ) : null}
    </div>
  );
}

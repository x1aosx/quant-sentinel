import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  ChevronRight,
  CircleDollarSign,
  Database,
  Gauge,
  Layers,
  Loader2,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import { api } from '../api/client';
import { KlineChart } from '../components/KlineChart';
import type {
  DatasetBar,
  DatasetSummary,
  MultiTimeframeAnalysisResult,
  SrLevel,
  StockAnalysisAISummary,
  StockAnalysisDecision,
  StockAnalysisResponse,
  TimeframeAnalysisResult,
} from '../types';

type AnalysisView = 'overview' | 'timeframes' | 'quant' | 'decision' | 'raw';

interface RoleEntry {
  role: string;
  timeframe: string;
}

interface StockProductGroup {
  symbol: string;
  name: string;
  timeframes: string[];
}

const DEFAULT_PROFILE: Record<string, string> = {
  strategic: '1d',
  tactical: '1h',
  confirmation: '30m',
  execution: '15m',
};

const VIEWS: Array<{
  key: AnalysisView;
  label: string;
  icon: typeof Gauge;
}> = [
  { key: 'overview', label: '综合概览', icon: Gauge },
  { key: 'timeframes', label: '多周期', icon: Layers },
  { key: 'quant', label: '量价结构', icon: TrendingUp },
  { key: 'decision', label: '决策', icon: ShieldAlert },
  { key: 'raw', label: '原始数据', icon: Database },
];

const ROLE_LABELS: Record<string, string> = {
  strategic: '战略趋势',
  tactical: '日内主周期',
  confirmation: '确认周期',
  execution: '执行周期',
};

const TIMEFRAME_LABELS: Record<string, string> = {
  '1m': '1分钟',
  '5m': '5分钟',
  '15m': '15分钟',
  '30m': '30分钟',
  '1h': '1小时',
  '4h': '4小时',
  '1d': '日线',
  '1w': '周线',
};

const TREND_LABELS: Record<string, string> = {
  STRONG_BULLISH: '强势上涨',
  BULLISH: '多头',
  NEUTRAL: '中性',
  BEARISH: '空头',
  STRONG_BEARISH: '强势下跌',
};

const PHASE_LABELS: Record<string, string> = {
  TRENDING: '趋势运行',
  PULLBACK: '回调',
  CONSOLIDATION: '震荡整理',
  BREAKOUT: '突破',
  REVERSAL: '反转',
  EXHAUSTION: '趋势衰竭',
  UNKNOWN: '未知',
};

const ACTION_LABELS: Record<string, string> = {
  BUY: '买入',
  WAIT_BUY: '等待买入',
  HOLD: '持有',
  REDUCE: '减仓',
  WAIT_SELL: '等待卖出',
  SELL: '卖出',
  AVOID: '回避',
  WATCH: '观察',
  READY: '准备执行',
  TRIGGERED: '已触发',
  INVALIDATED: '已失效',
};

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

function groupStocks(datasets: DatasetSummary[]): StockProductGroup[] {
  const groups: StockProductGroup[] = [];
  const groupBySymbol = new Map<string, StockProductGroup>();

  datasets.forEach((dataset) => {
    const symbol = normalizeSymbol(dataset.symbol);
    if (!symbol) return;
    let group = groupBySymbol.get(symbol);
    if (!group) {
      group = {
        symbol: dataset.symbol.trim(),
        name: dataset.title?.trim() || dataset.symbol.trim(),
        timeframes: [],
      };
      groupBySymbol.set(symbol, group);
      groups.push(group);
    }
    if ((!group.name || group.name === group.symbol) && dataset.title?.trim()) {
      group.name = dataset.title.trim();
    }
    if (!group.timeframes.includes(dataset.timeframe)) {
      group.timeframes.push(dataset.timeframe);
    }
  });

  return groups;
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function fmtNumber(value: unknown, digits = 2, missing = '--'): string {
  const parsed = toFiniteNumber(value);
  if (parsed === null) return missing;
  return parsed.toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
  });
}

function fmtSignedPct(value: unknown, digits = 2): string {
  const parsed = toFiniteNumber(value);
  if (parsed === null) return '--';
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`;
}

function fmtConfidence(value: unknown): string {
  const parsed = toFiniteNumber(value);
  if (parsed === null) return '--';
  const percent = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${percent.toFixed(percent >= 10 ? 0 : 1)}%`;
}

function fmtTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function enumLabel(value: unknown, labels: Record<string, string>): string {
  const key = String(value ?? '').trim();
  if (!key) return '--';
  const normalized = key.toUpperCase();
  return labels[normalized] ?? normalized.replace(/_/g, ' ');
}

function timeframeLabel(timeframe: string): string {
  return TIMEFRAME_LABELS[timeframe.toLowerCase()] ?? timeframe;
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role.toLowerCase()] ?? role;
}

function toneClass(value: unknown): string {
  const normalized = String(value ?? '').toUpperCase();
  if (
    normalized.includes('BULL') ||
    normalized.includes('BUY') ||
    normalized.includes('LONG') ||
    normalized.includes('HEALTH') ||
    normalized.includes('TREND')
  ) {
    return 'market-up';
  }
  if (
    normalized.includes('BEAR') ||
    normalized.includes('SELL') ||
    normalized.includes('SHORT') ||
    normalized.includes('WEAK') ||
    normalized.includes('INVALID')
  ) {
    return 'market-down';
  }
  return 'market-flat';
}

function changeToneClass(value: unknown): string {
  const parsed = toFiniteNumber(value);
  if (parsed === null || parsed === 0) return 'market-flat';
  return parsed > 0 ? 'market-up' : 'market-down';
}

function decisionBadgeClass(action: unknown): string {
  const normalized = String(action ?? '').toUpperCase();
  if (['BUY', 'HOLD'].includes(normalized)) return 'badge badge-ok';
  if (['SELL', 'REDUCE', 'AVOID', 'INVALIDATED'].includes(normalized)) {
    return 'badge badge-danger';
  }
  if (['WAIT_BUY', 'READY', 'TRIGGERED'].includes(normalized)) {
    return 'badge badge-warn';
  }
  return 'badge badge-neutral';
}

function getTimeframeResult(
  timeframes: Record<string, TimeframeAnalysisResult> | undefined,
  timeframe: string,
): TimeframeAnalysisResult | undefined {
  if (!timeframes) return undefined;
  const target = timeframe.trim().toLowerCase();
  const key = Object.keys(timeframes).find(
    (candidate) => candidate.trim().toLowerCase() === target,
  );
  return key ? timeframes[key] : undefined;
}

function extractCandles(rawResult: Record<string, unknown> | undefined): DatasetBar[] {
  const value = rawResult?.candles;
  if (!Array.isArray(value)) return [];

  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const source = item as Record<string, unknown>;
    const open = toFiniteNumber(source.open);
    const high = toFiniteNumber(source.high);
    const low = toFiniteNumber(source.low);
    const close = toFiniteNumber(source.close);
    const sessionValue =
      source.session_id ?? source.time ?? source.timestamp ?? source.datetime;
    const sessionId =
      typeof sessionValue === 'string' || typeof sessionValue === 'number'
        ? String(sessionValue)
        : '';
    if (!sessionId || open === null || high === null || low === null || close === null) {
      return [];
    }
    return [
      {
        session_id: sessionId,
        open,
        high,
        low,
        close,
        volume: toFiniteNumber(source.volume) ?? 0,
      },
    ];
  });
}

function buildLevels(result: TimeframeAnalysisResult | undefined): SrLevel[] {
  if (!result) return [];
  const supports = Array.isArray(result.support_levels) ? result.support_levels : [];
  const resistances = Array.isArray(result.resistance_levels) ? result.resistance_levels : [];
  return [
    ...supports.flatMap((price) =>
      toFiniteNumber(price) === null
        ? []
        : [
            {
              zone_type: 'support' as const,
              center: Number(price),
              low: Number(price),
              high: Number(price),
            },
          ],
    ),
    ...resistances.flatMap((price) =>
      toFiniteNumber(price) === null
        ? []
        : [
            {
              zone_type: 'resistance' as const,
              center: Number(price),
              low: Number(price),
              high: Number(price),
            },
          ],
    ),
  ];
}

function nearestLevel(
  levels: number[] | undefined,
  currentPrice: number | null,
  kind: 'support' | 'resistance',
): number | null {
  const valid = (levels ?? []).flatMap((value) => {
    const parsed = toFiniteNumber(value);
    return parsed === null ? [] : [parsed];
  });
  if (!valid.length) return null;
  if (currentPrice === null) {
    return kind === 'support' ? Math.max(...valid) : Math.min(...valid);
  }
  if (kind === 'support') {
    const below = valid.filter((value) => value <= currentPrice);
    return below.length ? Math.max(...below) : Math.min(...valid);
  }
  const above = valid.filter((value) => value >= currentPrice);
  return above.length ? Math.min(...above) : Math.max(...valid);
}

function roleState(
  role: string,
  timeframeResult: TimeframeAnalysisResult | undefined,
  multiTimeframe: MultiTimeframeAnalysisResult,
): string {
  switch (role.toLowerCase()) {
    case 'strategic':
      return multiTimeframe.strategic_trend || timeframeResult?.trend || '--';
    case 'tactical':
      return multiTimeframe.intraday_state || timeframeResult?.trend || '--';
    case 'confirmation':
      return multiTimeframe.confirmation_state || timeframeResult?.trend || '--';
    case 'execution':
      return multiTimeframe.execution_state || timeframeResult?.trend || '--';
    default:
      return timeframeResult?.trend || '--';
  }
}

function roleScore(
  role: string,
  timeframeResult: TimeframeAnalysisResult | undefined,
  multiTimeframe: MultiTimeframeAnalysisResult,
): number | null {
  if (role.toLowerCase() === 'strategic') {
    return toFiniteNumber(multiTimeframe.strategic_score) ?? timeframeResult?.trend_score ?? null;
  }
  return timeframeResult?.trend_score ?? null;
}

function formatAiValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value.map(formatAiValue).filter(Boolean).join('；');
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const title = record.title ?? record.name ?? record.label ?? record.scenario;
    const detail =
      record.description ?? record.detail ?? record.summary ?? record.reasoning ?? record.content;
    if (title !== undefined && detail !== undefined) {
      return `${formatAiValue(title)}：${formatAiValue(detail)}`;
    }
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function isStockAnalysisResponse(
  value: StockAnalysisAISummary | StockAnalysisResponse,
): value is StockAnalysisResponse {
  return 'timeframes' in value && 'decision' in value && 'multi_timeframe' in value;
}

function TextItems({
  items,
  empty = '暂无数据',
}: {
  items: unknown;
  empty?: string;
}) {
  const values = Array.isArray(items) ? items.map(formatAiValue).filter(Boolean) : [];
  if (!values.length) return <span className="muted">{empty}</span>;
  return (
    <ul className="analysis-text-list">
      {values.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  );
}

function OverviewPanel({
  analysis,
  roleEntries,
  primaryResult,
  aiPending,
  aiError,
  onGenerateAI,
}: {
  analysis: StockAnalysisResponse;
  roleEntries: RoleEntry[];
  primaryResult: TimeframeAnalysisResult | undefined;
  aiPending: boolean;
  aiError: string;
  onGenerateAI: () => void;
}) {
  const multi = analysis.multi_timeframe;
  const decision = analysis.decision;
  const currentPrice = analysis.quote?.current_price ?? null;
  const nearestSupport = nearestLevel(primaryResult?.support_levels, currentPrice, 'support');
  const nearestResistance = nearestLevel(
    primaryResult?.resistance_levels,
    currentPrice,
    'resistance',
  );

  return (
    <div className="stack">
      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Gauge size={16} />
            当前综合状态
          </span>
          <span className="tag">{enumLabel(multi.summary_state, {})}</span>
        </div>
        <div className="analysis-kpi-grid">
          <div>
            <span className="label">中期趋势</span>
            <strong className={toneClass(multi.strategic_trend)}>
              {enumLabel(multi.strategic_trend, TREND_LABELS)}
            </strong>
            <span className="muted">{fmtNumber(multi.strategic_score, 0)} 分</span>
          </div>
          <div>
            <span className="label">日内状态</span>
            <strong className={toneClass(multi.intraday_state)}>
              {enumLabel(multi.intraday_state, TREND_LABELS)}
            </strong>
            <span className="muted">
              {enumLabel(multi.confirmation_state, PHASE_LABELS)}
            </span>
          </div>
          <div>
            <span className="label">操作状态</span>
            <strong className={toneClass(decision?.action)}>
              {enumLabel(decision?.action, ACTION_LABELS)}
            </strong>
            <span className="muted">
              {enumLabel(multi.execution_state, ACTION_LABELS)}
            </span>
          </div>
          <div>
            <span className="label">置信度</span>
            <strong>{fmtConfidence(decision?.confidence)}</strong>
            <span className="muted">决策置信度</span>
          </div>
          <div>
            <span className="label">周期一致性</span>
            <strong>{fmtNumber(multi.alignment_score, 0)}%</strong>
            <span className="muted">冲突 {fmtNumber(multi.conflict_score, 0)}%</span>
          </div>
        </div>
        <div className="analysis-score-band">
          <div>
            <span>多头评分</span>
            <strong>{fmtNumber(multi.bullish_score, 0)}</strong>
          </div>
          <div>
            <span>空头评分</span>
            <strong>{fmtNumber(multi.bearish_score, 0)}</strong>
          </div>
          <div>
            <span>缺失周期</span>
            <strong>
              {multi.missing_timeframes?.length
                ? multi.missing_timeframes.map(timeframeLabel).join('、')
                : '无'}
            </strong>
          </div>
          <div>
            <span>更新时间</span>
            <strong>{fmtTimestamp(analysis.generated_at)}</strong>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">周期摘要</span>
          <span className="tag">
            {analysis.from_cache ? '缓存结果' : '实时计算'}
          </span>
        </div>
        <div className="analysis-role-grid">
          {roleEntries.map(({ role, timeframe }) => {
            const result = getTimeframeResult(analysis.timeframes, timeframe);
            const state = roleState(role, result, multi);
            const score = roleScore(role, result, multi);
            return (
              <div key={`${role}-${timeframe}`} className="analysis-role-item">
                <div className="analysis-role-head">
                  <strong>{roleLabel(role)}</strong>
                  <span className="tag">{timeframeLabel(timeframe)}</span>
                </div>
                <div className={`analysis-role-state ${toneClass(state)}`}>
                  {enumLabel(state, TREND_LABELS)}
                </div>
                <div className="analysis-role-meta">
                  <span>{enumLabel(result?.phase, PHASE_LABELS)}</span>
                  <span>{score === null ? '--' : `${fmtNumber(score, 0)} 分`}</span>
                </div>
                <div className="analysis-role-submeta">
                  <span>动量 {fmtNumber(result?.momentum_score, 0)}</span>
                  <span>置信度 {fmtConfidence(result?.confidence)}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <span className="section-title-main">
              <CircleDollarSign size={16} />
              最近支撑与压力
            </span>
            <span className="tag">
              {timeframeLabel(primaryResult?.timeframe ?? roleEntries[0]?.timeframe ?? '--')}
            </span>
          </div>
          <div className="analysis-level-grid">
            <div>
              <span className="label">最近支撑</span>
              <strong className="support-value">{fmtNumber(nearestSupport, 4)}</strong>
            </div>
            <div>
              <span className="label">当前价格</span>
              <strong>{fmtNumber(currentPrice, 4)}</strong>
            </div>
            <div>
              <span className="label">最近压力</span>
              <strong className="resistance-value">{fmtNumber(nearestResistance, 4)}</strong>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <span className="section-title-main">
              <BrainCircuit size={16} />
              AI 综合结论
            </span>
            {analysis.ai_summary ? (
              <span className="badge badge-info">
                {analysis.ai_summary.source || analysis.ai_summary.status || 'AI'}
              </span>
            ) : (
              <button
                className="button button-primary"
                type="button"
                onClick={onGenerateAI}
                disabled={aiPending}
              >
                {aiPending ? <Loader2 className="spin" size={14} /> : <Sparkles size={14} />}
                {aiPending ? '生成中' : '生成 AI 结论'}
              </button>
            )}
          </div>
          {aiError ? (
            <div className="analysis-error">
              <AlertTriangle size={15} />
              {aiError}
            </div>
          ) : null}
          {!analysis.ai_summary ? (
            <div className="analysis-ai-empty">
              尚未生成 AI 综合结论。
            </div>
          ) : (
            <div className="analysis-ai-content">
              <p>{analysis.ai_summary.summary || '暂无结论摘要。'}</p>
              {analysis.ai_summary.cycle_explanation ? (
                <p>{analysis.ai_summary.cycle_explanation}</p>
              ) : null}
              {formatAiValue(analysis.ai_summary.scenarios) ? (
                <div className="analysis-ai-section">
                  <span className="label">情景推演</span>
                  <p>{formatAiValue(analysis.ai_summary.scenarios)}</p>
                </div>
              ) : null}
              <div className="analysis-ai-columns">
                <div>
                  <span className="label">触发条件</span>
                  <TextItems items={analysis.ai_summary.trigger_conditions} />
                </div>
                <div>
                  <span className="label">失效条件</span>
                  <TextItems items={analysis.ai_summary.invalid_conditions} />
                </div>
                <div>
                  <span className="label">风险提示</span>
                  <TextItems items={analysis.ai_summary.risks} />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MultiTimeframePanel({
  analysis,
  roleEntries,
  timeframeOrder,
}: {
  analysis: StockAnalysisResponse;
  roleEntries: RoleEntry[];
  timeframeOrder: string[];
}) {
  const multi = analysis.multi_timeframe;
  return (
    <div className="stack">
      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Layers size={16} />
            多周期状态
          </span>
          <span className="row">
            <span className="tag">一致性 {fmtNumber(multi.alignment_score, 0)}%</span>
            <span className="tag">冲突 {fmtNumber(multi.conflict_score, 0)}%</span>
          </span>
        </div>
        <div className="table-wrap">
          <table className="table stock-analysis-table">
            <thead>
              <tr>
                <th>周期</th>
                <th>角色</th>
                <th>趋势</th>
                <th>阶段</th>
                <th>动量</th>
                <th>波动</th>
                <th>量价</th>
                <th>价格结构</th>
                <th>支撑</th>
                <th>压力</th>
                <th>置信度</th>
                <th>信号</th>
              </tr>
            </thead>
            <tbody>
              {timeframeOrder.map((timeframe) => {
                const result = getTimeframeResult(analysis.timeframes, timeframe);
                const role = roleEntries.find(
                  (entry) => entry.timeframe.toLowerCase() === timeframe.toLowerCase(),
                )?.role;
                return (
                  <tr key={timeframe}>
                    <td>
                      <strong>{timeframeLabel(timeframe)}</strong>
                    </td>
                    <td>{role ? roleLabel(role) : '--'}</td>
                    <td className={toneClass(result?.trend)}>
                      {enumLabel(result?.trend, TREND_LABELS)}
                    </td>
                    <td>{enumLabel(result?.phase, PHASE_LABELS)}</td>
                    <td>{fmtNumber(result?.momentum_score, 0)}</td>
                    <td>{fmtNumber(result?.volatility_score, 0)}</td>
                    <td>{result?.volume_state || '--'}</td>
                    <td>{result?.price_structure || '--'}</td>
                    <td className="support-value">
                      {result?.support_levels?.length
                        ? fmtNumber(result.support_levels[0], 4)
                        : '--'}
                    </td>
                    <td className="resistance-value">
                      {result?.resistance_levels?.length
                        ? fmtNumber(result.resistance_levels[0], 4)
                        : '--'}
                    </td>
                    <td>{fmtConfidence(result?.confidence)}</td>
                    <td>
                      <div className="analysis-signal-list">
                        {(result?.signals ?? []).length
                          ? result?.signals.map((signal) => (
                              <span key={signal} className="tag">
                                {signal}
                              </span>
                            ))
                          : '--'}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <div className="section-title">周期角色</div>
        <div className="analysis-role-grid">
          {roleEntries.map(({ role, timeframe }) => {
            const result = getTimeframeResult(analysis.timeframes, timeframe);
            const state = roleState(role, result, multi);
            return (
              <div className="analysis-role-item" key={`${role}-${timeframe}`}>
                <div className="analysis-role-head">
                  <strong>{roleLabel(role)}</strong>
                  <span className="tag">{timeframeLabel(timeframe)}</span>
                </div>
                <div className={`analysis-role-state ${toneClass(state)}`}>
                  {enumLabel(state, TREND_LABELS)}
                </div>
                <div className="analysis-role-submeta">
                  <span>{enumLabel(result?.phase, PHASE_LABELS)}</span>
                  <span>{fmtConfidence(result?.confidence)}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function QuantStructurePanel({
  analysis,
  roleEntries,
  timeframeOrder,
  selectedTimeframe,
  onSelectTimeframe,
}: {
  analysis: StockAnalysisResponse;
  roleEntries: RoleEntry[];
  timeframeOrder: string[];
  selectedTimeframe: string;
  onSelectTimeframe: (timeframe: string) => void;
}) {
  const activeTimeframe =
    selectedTimeframe &&
    getTimeframeResult(analysis.timeframes, selectedTimeframe)
      ? selectedTimeframe
      : timeframeOrder[0] ?? '';
  const result = getTimeframeResult(analysis.timeframes, activeTimeframe);
  const candles = useMemo(() => extractCandles(result?.raw_result), [result]);
  const levels = useMemo(() => buildLevels(result), [result]);
  const role = roleEntries.find(
    (entry) => entry.timeframe.toLowerCase() === activeTimeframe.toLowerCase(),
  )?.role;

  return (
    <div className="stack">
      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <TrendingUp size={16} />
            周期量价结构
          </span>
          <span className="row">
            {role ? <span className="tag">{roleLabel(role)}</span> : null}
            <span className="tag">{timeframeLabel(activeTimeframe)}</span>
          </span>
        </div>
        <div className="segmented segmented-scroll">
          {timeframeOrder.map((timeframe) => (
            <button
              key={timeframe}
              type="button"
              className={timeframe === activeTimeframe ? 'button button-primary' : 'button'}
              onClick={() => onSelectTimeframe(timeframe)}
            >
              {timeframeLabel(timeframe)}
            </button>
          ))}
        </div>
        <div className="analysis-metric-grid">
          <div>
            <span className="label">趋势</span>
            <strong className={toneClass(result?.trend)}>
              {enumLabel(result?.trend, TREND_LABELS)}
            </strong>
          </div>
          <div>
            <span className="label">阶段</span>
            <strong>{enumLabel(result?.phase, PHASE_LABELS)}</strong>
          </div>
          <div>
            <span className="label">量价状态</span>
            <strong>{result?.volume_state || '--'}</strong>
          </div>
          <div>
            <span className="label">价格结构</span>
            <strong>{result?.price_structure || '--'}</strong>
          </div>
          <div>
            <span className="label">趋势评分</span>
            <strong>{fmtNumber(result?.trend_score, 0)}</strong>
          </div>
          <div>
            <span className="label">置信度</span>
            <strong>{fmtConfidence(result?.confidence)}</strong>
          </div>
        </div>
        {result?.signals?.length ? (
          <div className="analysis-signal-list">
            {result.signals.map((signal) => (
              <span key={signal} className="tag">
                {signal}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">K 线与关键价位</span>
          <span className="tag">{candles.length ? `${candles.length} 根` : '无K线'}</span>
        </div>
        {candles.length ? (
          <KlineChart candles={candles.slice(-240)} levels={levels} />
        ) : (
          <div className="empty">当前周期没有可展示的 K 线数据。</div>
        )}
      </div>
    </div>
  );
}

function DecisionPanel({ decision }: { decision: StockAnalysisDecision | undefined }) {
  return (
    <div className="stack">
      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <ShieldAlert size={16} />
            量化决策
          </span>
          <span className={decisionBadgeClass(decision?.action)}>
            {enumLabel(decision?.action, ACTION_LABELS)}
          </span>
        </div>
        <div className="analysis-decision-metrics">
          <div>
            <span className="label">置信度</span>
            <strong>{fmtConfidence(decision?.confidence)}</strong>
          </div>
          <div>
            <span className="label">入场</span>
            <strong>{fmtNumber(decision?.entry, 4)}</strong>
          </div>
          <div>
            <span className="label">止损</span>
            <strong>{fmtNumber(decision?.stop, 4)}</strong>
          </div>
          <div>
            <span className="label">目标</span>
            <strong>{fmtNumber(decision?.target, 4)}</strong>
          </div>
          <div>
            <span className="label">风险回报比</span>
            <strong>{fmtNumber(decision?.risk_reward, 2)}</strong>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">条件与风险</div>
        <div className="analysis-decision-columns">
          <div>
            <span className="label">触发条件</span>
            <TextItems items={decision?.trigger_conditions} />
          </div>
          <div>
            <span className="label">失效条件</span>
            <TextItems items={decision?.invalid_conditions} />
          </div>
          <div>
            <span className="label">风险标记</span>
            <TextItems items={decision?.risk_flags} />
          </div>
        </div>
        <div className="analysis-decision-reasons">
          <span className="label">原因代码</span>
          <div className="analysis-signal-list">
            {(decision?.reason_codes ?? []).length
              ? decision?.reason_codes.map((code) => (
                  <span key={code} className="tag">
                    {code}
                  </span>
                ))
              : '--'}
          </div>
        </div>
      </div>
    </div>
  );
}

function RawDataPanel({ analysis }: { analysis: StockAnalysisResponse }) {
  return (
    <div className="panel">
      <div className="section-title">
        <span className="section-title-main">
          <Database size={16} />
          完整分析数据
        </span>
        <span className="tag">{fmtTimestamp(analysis.generated_at)}</span>
      </div>
      <pre className="raw-prompt stock-analysis-raw">
        {JSON.stringify(analysis, null, 2)}
      </pre>
    </div>
  );
}

export function StockAnalysisWorkbenchPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [view, setView] = useState<AnalysisView>('overview');
  const [selectedTimeframe, setSelectedTimeframe] = useState('');

  const datasetsQuery = useQuery({
    queryKey: ['datasets'],
    queryFn: api.listDatasets,
  });
  const groups = useMemo(
    () => groupStocks(datasetsQuery.data?.items ?? []),
    [datasetsQuery.data?.items],
  );
  const filteredGroups = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return groups;
    return groups.filter(
      (group) =>
        group.symbol.toLowerCase().includes(keyword) ||
        group.name.toLowerCase().includes(keyword),
    );
  }, [groups, search]);
  const effectiveSymbol =
    selectedSymbol && groups.some((group) => group.symbol === selectedSymbol)
      ? selectedSymbol
      : groups[0]?.symbol ?? '';

  const analysisQuery = useQuery({
    queryKey: ['stock-analysis', effectiveSymbol],
    queryFn: () =>
      api.getStockAnalysis(effectiveSymbol, {
        refresh: false,
        include_ai: false,
      }),
    enabled: Boolean(effectiveSymbol),
  });
  const analysis = analysisQuery.data;

  const refreshMutation = useMutation({
    mutationFn: (symbol: string) =>
      api.getStockAnalysis(symbol, { refresh: true, include_ai: false }),
    onSuccess: (data) => {
      queryClient.setQueryData(['stock-analysis', data.symbol], data);
    },
  });

  const aiMutation = useMutation({
    mutationFn: (symbol: string) => api.generateStockAnalysisAI(symbol),
    onSuccess: (payload, symbol) => {
      const current = queryClient.getQueryData<StockAnalysisResponse>([
        'stock-analysis',
        symbol,
      ]);
      if (isStockAnalysisResponse(payload)) {
        queryClient.setQueryData(['stock-analysis', payload.symbol], payload);
        return;
      }
      if (current) {
        queryClient.setQueryData(['stock-analysis', symbol], {
          ...current,
          ai_summary: payload,
        });
      }
    },
  });

  const profile = analysis?.multi_timeframe?.metadata?.profile;
  const roleProfile = useMemo(() => {
    if (profile && Object.keys(profile).length) return profile;
    return DEFAULT_PROFILE;
  }, [profile]);
  const roleEntries = useMemo<RoleEntry[]>(
    () =>
      Object.entries(roleProfile)
        .filter((entry): entry is [string, string] => typeof entry[1] === 'string')
        .map(([role, timeframe]) => ({ role, timeframe })),
    [roleProfile],
  );
  const timeframeOrder = useMemo(() => {
    const known = new Set(roleEntries.map((entry) => entry.timeframe.toLowerCase()));
    return [
      ...roleEntries.map((entry) => entry.timeframe),
      ...Object.keys(analysis?.timeframes ?? {}).filter(
        (timeframe) => !known.has(timeframe.toLowerCase()),
      ),
    ];
  }, [analysis?.timeframes, roleEntries]);
  const primaryRole = roleEntries.find((entry) => entry.role.toLowerCase() === 'strategic');
  const primaryResult = analysis
    ? getTimeframeResult(
        analysis.timeframes,
        primaryRole?.timeframe ?? roleEntries[0]?.timeframe ?? '',
      )
    : undefined;

  const selectSymbol = (symbol: string) => {
    setSelectedSymbol(symbol);
    setView('overview');
    setSelectedTimeframe('');
    aiMutation.reset();
  };

  const listLoading = datasetsQuery.isLoading;
  const listError = datasetsQuery.isError
    ? `股票列表加载失败：${(datasetsQuery.error as Error).message}`
    : '';
  const analysisError = analysisQuery.isError
    ? `股票分析加载失败：${(analysisQuery.error as Error).message}`
    : '';
  const refreshError = refreshMutation.isError
    ? `刷新失败：${(refreshMutation.error as Error).message}`
    : '';
  const aiError = aiMutation.isError ? `AI 生成失败：${(aiMutation.error as Error).message}` : '';

  return (
    <div className="stack">
      <div className="page-header stock-analysis-page-header">
        <div>
          <h1>股票分析工作台</h1>
          <div className="muted">多周期量化、量价结构与按需 AI 结论统一在一个视图中。</div>
        </div>
        <span className="badge badge-info">simulation only</span>
      </div>

      <div className="stock-analysis-layout">
        <aside className="panel stock-list-panel">
          <div className="section-title">
            <span className="section-title-main">
              <Activity size={16} />
              股票列表
            </span>
            <span className="tag">{groups.length}</span>
          </div>
          <label className="stock-list-search">
            <Search size={14} />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索代码或名称"
              aria-label="搜索股票"
            />
          </label>
          {listLoading ? (
            <div className="empty">正在加载股票...</div>
          ) : listError ? (
            <div className="empty">{listError}</div>
          ) : filteredGroups.length ? (
            <div className="stock-list">
              {filteredGroups.map((group) => {
                const active = group.symbol === effectiveSymbol;
                return (
                  <button
                    key={group.symbol}
                    type="button"
                    className={`stock-list-item${active ? ' active' : ''}`}
                    onClick={() => selectSymbol(group.symbol)}
                  >
                    <span className="stock-list-main">
                      <strong>{group.symbol}</strong>
                      <span>{group.name !== group.symbol ? group.name : '--'}</span>
                    </span>
                    <span className="stock-list-meta">
                      {group.timeframes.map(timeframeLabel).join(' / ')}
                      <ChevronRight size={14} />
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="empty">
              {search.trim() ? '没有匹配的股票。' : '暂无可分析股票，请先在数据中心导入行情。'}
            </div>
          )}
        </aside>

        <section className="stack stock-analysis-workspace">
          {!effectiveSymbol ? (
            <div className="empty">暂无可分析股票。</div>
          ) : analysisQuery.isLoading || (!analysis && !analysisError) ? (
            <div className="empty">正在加载量化分析...</div>
          ) : analysisError ? (
            <div className="empty">{analysisError}</div>
          ) : analysis ? (
            <>
              <div className="stock-quote-bar">
                <div className="stock-quote-identity">
                  <span className="badge badge-info">{analysis.symbol}</span>
                  <div>
                    <h2>{analysis.name || analysis.symbol}</h2>
                    <span>
                      {analysis.quote?.timeframe ?? '--'} ·{' '}
                      {analysis.quote?.last_session || '--'}
                    </span>
                  </div>
                </div>
                <div className="stock-quote-price">
                  <span>现价</span>
                  <strong>{fmtNumber(analysis.quote?.current_price, 4)}</strong>
                </div>
                <div className={`stock-quote-change ${changeToneClass(analysis.quote?.change_pct)}`}>
                  <span>涨跌</span>
                  <strong>{fmtSignedPct(analysis.quote?.change_pct)}</strong>
                </div>
                <div className="stock-quote-actions">
                  <span className="muted">{fmtTimestamp(analysis.generated_at)}</span>
                  <button
                    className="button"
                    type="button"
                    onClick={() => refreshMutation.mutate(analysis.symbol)}
                    disabled={analysisQuery.isFetching || refreshMutation.isPending}
                  >
                    <RefreshCw
                      className={refreshMutation.isPending ? 'spin' : undefined}
                      size={14}
                    />
                    {refreshMutation.isPending ? '刷新中' : '刷新'}
                  </button>
                </div>
              </div>

              {refreshError ? (
                <div className="analysis-error">
                  <AlertTriangle size={15} />
                  {refreshError}
                </div>
              ) : null}

              <div className="segmented segmented-scroll stock-analysis-tabs">
                {VIEWS.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      className={view === item.key ? 'button button-primary' : 'button'}
                      onClick={() => setView(item.key)}
                    >
                      <Icon size={14} />
                      {item.label}
                    </button>
                  );
                })}
              </div>

              {view === 'overview' ? (
                <OverviewPanel
                  analysis={analysis}
                  roleEntries={roleEntries}
                  primaryResult={primaryResult}
                  aiPending={aiMutation.isPending}
                  aiError={aiError}
                  onGenerateAI={() => aiMutation.mutate(analysis.symbol)}
                />
              ) : null}
              {view === 'timeframes' ? (
                <MultiTimeframePanel
                  analysis={analysis}
                  roleEntries={roleEntries}
                  timeframeOrder={timeframeOrder}
                />
              ) : null}
              {view === 'quant' ? (
                <QuantStructurePanel
                  analysis={analysis}
                  roleEntries={roleEntries}
                  timeframeOrder={timeframeOrder}
                  selectedTimeframe={selectedTimeframe}
                  onSelectTimeframe={setSelectedTimeframe}
                />
              ) : null}
              {view === 'decision' ? <DecisionPanel decision={analysis.decision} /> : null}
              {view === 'raw' ? <RawDataPanel analysis={analysis} /> : null}
            </>
          ) : null}
        </section>
      </div>
    </div>
  );
}

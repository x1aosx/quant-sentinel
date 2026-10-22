/**
 * 量化补充视图 —— 从原「股票分析工作台」迁移而来的确定性量化面板。
 *
 * 定位：AI 分析页的补充。AI（LLM）负责诊断/决策/未来走势，
 * 这里提供 quant 引擎测算出的多周期趋势、周期角色、支撑阻力与量价结构，
 * 供用户交叉验证 AI 结论。
 *
 * 刻意不包含「AI 综合结论」卡与决策卡：那两块与 AI 分析页的
 * decision 视图重复，且数据来源不同（quant 决策 vs stage2 决策），
 * 同时展示会给出两个互相矛盾的结论。
 */
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  CircleDollarSign,
  Database,
  Gauge,
  Layers,
  Loader2,
  AlertTriangle,
  RefreshCw,
  ShieldAlert,
  TrendingUp,
} from 'lucide-react';
import { api } from '../api/client';
import { KlineChart } from './KlineChart';
import type {
  DatasetBar,
  MultiTimeframeAnalysisResult,
  SrLevel,
  StockAnalysisDecision,
  StockAnalysisResponse,
  TimeframeAnalysisResult,
} from '../types';

const DEFAULT_PROFILE: Record<string, string> = {
  strategic: '1d',
  tactical: '1h',
  confirmation: '30m',
  execution: '15m',
};

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

type QuantTab = 'overview' | 'timeframes' | 'quant' | 'decision' | 'raw';

const QUANT_TABS: Array<{ key: QuantTab; label: string }> = [
  { key: 'overview', label: '综合概览' },
  { key: 'timeframes', label: '多周期' },
  { key: 'quant', label: '量价结构' },
  { key: 'decision', label: '量化决策' },
  { key: 'raw', label: '原始数据' },
];

interface RoleEntry {
  role: string;
  timeframe: string;
}

function toFiniteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fmtNumber(value: unknown, digits = 2, missing = '--'): string {
  const parsed = toFiniteNumber(value);
  return parsed === null ? missing : parsed.toFixed(digits);
}

function fmtConfidence(value: unknown): string {
  const parsed = toFiniteNumber(value);
  if (parsed === null) return '--';
  const normalized = parsed > 1 ? parsed / 100 : parsed;
  return `${(normalized * 100).toFixed(0)}%`;
}

function fmtTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

function enumLabel(value: unknown, labels: Record<string, string>): string {
  if (typeof value !== 'string' || !value) return '--';
  return labels[value] ?? value;
}

function timeframeLabel(timeframe: string): string {
  if (!timeframe) return '--';
  return TIMEFRAME_LABELS[timeframe.toLowerCase()] ?? timeframe;
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role.toLowerCase()] ?? role;
}

function toneClass(value: unknown): string {
  if (typeof value !== 'string') return '';
  const upper = value.toUpperCase();
  if (upper.includes('BULL') || upper === 'BUY' || upper === 'HOLD') return 'tone-up';
  if (upper.includes('BEAR') || upper === 'SELL' || upper === 'AVOID') return 'tone-down';
  return '';
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

function decisionBadgeClass(action: unknown): string {
  if (typeof action !== 'string') return 'badge badge-neutral';
  const upper = action.toUpperCase();
  if (upper === 'BUY' || upper === 'HOLD') return 'badge badge-ok';
  if (upper === 'SELL' || upper === 'REDUCE' || upper === 'AVOID') return 'badge badge-danger';
  if (upper === 'WAIT_BUY' || upper === 'WAIT_SELL' || upper === 'WATCH') {
    return 'badge badge-warn';
  }
  return 'badge badge-neutral';
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

function TextItems({ items, empty = '暂无数据' }: { items: unknown; empty?: string }) {
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
}: {
  analysis: StockAnalysisResponse;
  roleEntries: RoleEntry[];
  primaryResult: TimeframeAnalysisResult | undefined;
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
            <span className="muted">{enumLabel(multi.confirmation_state, PHASE_LABELS)}</span>
          </div>
          <div>
            <span className="label">操作状态</span>
            <strong className={toneClass(decision?.action)}>
              {enumLabel(decision?.action, ACTION_LABELS)}
            </strong>
            <span className="muted">{enumLabel(multi.execution_state, ACTION_LABELS)}</span>
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
          <span className="tag">{analysis.from_cache ? '缓存结果' : '实时计算'}</span>
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
    selectedTimeframe && getTimeframeResult(analysis.timeframes, selectedTimeframe)
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
            量化决策（引擎口径）
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
      <pre className="raw-prompt stock-analysis-raw">{JSON.stringify(analysis, null, 2)}</pre>
    </div>
  );
}

export function QuantSupplementPanel({
  symbol,
  enabled = true,
}: {
  symbol: string;
  enabled?: boolean;
}) {
  const [tab, setTab] = useState<QuantTab>('overview');
  const [selectedTimeframe, setSelectedTimeframe] = useState('');

  const query = useQuery({
    queryKey: ['quant-supplement', symbol],
    queryFn: () => api.getStockAnalysis(symbol, { include_ai: false }),
    enabled: enabled && Boolean(symbol),
    staleTime: 60_000,
  });

  const analysis = query.data;
  const roleEntries = useMemo<RoleEntry[]>(
    () =>
      Object.entries(DEFAULT_PROFILE)
        .filter((entry): entry is [string, string] => typeof entry[1] === 'string')
        .map(([role, timeframe]) => ({ role, timeframe })),
    [],
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

  if (!symbol) {
    return <div className="empty">先在上方选择股票，量化补充视图会同步展示该标的的多周期结构。</div>;
  }

  if (query.isLoading) {
    return (
      <div className="empty">
        <Loader2 className="spin" size={16} /> 加载量化结构…
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="analysis-error">
        <AlertTriangle size={15} />
        {(query.error as Error)?.message || '量化分析加载失败'}
        <button className="button" type="button" onClick={() => void query.refetch()}>
          <RefreshCw size={14} />
          重试
        </button>
      </div>
    );
  }

  if (!analysis) {
    return <div className="empty">该标的暂无可用的量化分析结果。</div>;
  }

  return (
    <div className="stack" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="segmented segmented-scroll">
          {QUANT_TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={tab === item.key ? 'button button-primary' : 'button'}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <button className="button" type="button" onClick={() => void query.refetch()}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>

      <div className="muted" style={{ fontSize: 12 }}>
        确定性量化引擎输出（非 LLM）。用于交叉验证 AI 结论：若两者方向冲突，说明该标的处于
        分歧区，宜降低仓位或等待确认。
      </div>

      {tab === 'overview' ? (
        <OverviewPanel
          analysis={analysis}
          roleEntries={roleEntries}
          primaryResult={primaryResult}
        />
      ) : null}
      {tab === 'timeframes' ? (
        <MultiTimeframePanel
          analysis={analysis}
          roleEntries={roleEntries}
          timeframeOrder={timeframeOrder}
        />
      ) : null}
      {tab === 'quant' ? (
        <QuantStructurePanel
          analysis={analysis}
          roleEntries={roleEntries}
          timeframeOrder={timeframeOrder}
          selectedTimeframe={selectedTimeframe}
          onSelectTimeframe={setSelectedTimeframe}
        />
      ) : null}
      {tab === 'decision' ? (
        <div className="stack">
          <div className="muted" style={{ fontSize: 12 }}>
            这是 quant 规则引擎的独立决策口径（8 值行动词表）。若与 AI 决策视图方向不同，
            说明模型与规则引擎存在分歧，不是数据错误；两者共振时信号更可信。
          </div>
          <DecisionPanel decision={analysis.decision} />
        </div>
      ) : null}
      {tab === 'raw' ? <RawDataPanel analysis={analysis} /> : null}
    </div>
  );
}

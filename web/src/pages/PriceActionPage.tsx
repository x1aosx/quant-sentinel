import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  CandlestickChart,
  CircleDollarSign,
  Gauge,
  ListChecks,
  Loader2,
  Play,
  ScanSearch,
  TrendingUp,
} from 'lucide-react';
import { KlineChart } from '../components/KlineChart';
import type { DatasetSummary, PaAnalysisResult } from '../types';

const API_BASE = '/api/v1';

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown; message?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (typeof body.message === 'string') detail = body.message;
    } catch {
      // keep HTTP status as the error detail
    }
    throw new Error(`请求失败: ${detail}`);
  }
  return (await res.json()) as T;
}

const listDatasets = () => requestJson<{ items: DatasetSummary[] }>('/datasets');

function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

function fmt100(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return `${value.toFixed(digits)}%`;
}

function fmtDirectionScore(direction?: string, score?: number): string {
  const label = direction ? DIRECTION_LABELS[direction] ?? direction : '--';
  if (score === undefined || !Number.isFinite(score)) return label;
  return `${label} ${score > 0 ? '+' : ''}${score}`;
}

const DIRECTION_LABELS: Record<string, string> = {
  bullish: '偏多',
  bearish: '偏空',
  neutral: '中性',
};

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

export function PriceActionPage() {
  const [datasetId, setDatasetId] = useState('');
  const [lookback, setLookback] = useState('250');
  const [riskFraction, setRiskFraction] = useState('0.01');
  const [minRr, setMinRr] = useState('1.5');
  const [stance, setStance] = useState<'conservative' | 'balanced' | 'aggressive'>('balanced');
  const [pageError, setPageError] = useState('');

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: listDatasets });
  const datasets = datasetsQuery.data?.items ?? [];

  useEffect(() => {
    if (!datasetId && datasets.length) {
      setDatasetId(datasets[0].id);
    }
  }, [datasetId, datasets]);

  const analysis = useMutation({
    mutationFn: (payload: {
      dataset_id: string;
      lookback?: number;
      risk_fraction?: number;
      min_rr?: number;
      stance?: 'conservative' | 'balanced' | 'aggressive';
    }) => requestJson<PaAnalysisResult>('/analysis/price-action', { method: 'POST', body: JSON.stringify(payload) }),
  });

  const handleAnalyze = () => {
    setPageError('');
    if (!datasetId) {
      setPageError('请先选择数据集（可先到数据中心导入）。');
      return;
    }
    try {
      const parsedLookback = parseOptionalInt(lookback, 'lookback');
      const parsedRiskFraction = parseOptionalFloat(riskFraction, 'risk_fraction', 0.0001, 0.2);
      const parsedMinRr = parseOptionalFloat(minRr, 'min_rr', 0, 20);
      analysis.mutate({
        dataset_id: datasetId,
        ...(parsedLookback !== undefined ? { lookback: parsedLookback } : {}),
        ...(parsedRiskFraction !== undefined ? { risk_fraction: parsedRiskFraction } : {}),
        ...(parsedMinRr !== undefined ? { min_rr: parsedMinRr } : {}),
        stance,
      });
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    }
  };

  const result = analysis.data;
  const context = result?.market_context;
  const features = result?.features;
  const decision = result?.decision;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>价格行为分析</h1>
          <div className="muted">基于所选数据集观察结构、突破与形态，仅用于研究与演示。</div>
        </div>
        {result ? (
          <div className="row">
            <span className="badge badge-info">{result.symbol}</span>
            <span className="tag">{result.timeframe}</span>
            <span className="tag">{result.bars_used} 根K线</span>
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="row">
            <CandlestickChart size={15} />
            分析参数
          </span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>数据集</span>
            <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
              {datasets.length === 0 ? <option value="">暂无数据集</option> : null}
              {datasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.symbol} · {dataset.timeframe} · {dataset.bar_count} 根
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Lookback</span>
            <input value={lookback} onChange={(e) => setLookback(e.target.value)} placeholder="如 250，可留空" />
          </label>
          <label className="field">
            <span>Risk fraction</span>
            <input value={riskFraction} onChange={(e) => setRiskFraction(e.target.value)} placeholder="如 0.01" />
          </label>
          <label className="field">
            <span>最小 RR</span>
            <input value={minRr} onChange={(e) => setMinRr(e.target.value)} placeholder="如 1.5" />
          </label>
          <label className="field">
            <span>分析倾向</span>
            <select value={stance} onChange={(e) => setStance(e.target.value as 'conservative' | 'balanced' | 'aggressive')}>
              <option value="conservative">保守 conservative</option>
              <option value="balanced">均衡 balanced</option>
              <option value="aggressive">积极 aggressive</option>
            </select>
          </label>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="button button-primary" onClick={handleAnalyze} disabled={analysis.isPending || !datasetId}>
            {analysis.isPending ? <Loader2 size={14} /> : <Play size={14} />}
            {analysis.isPending ? '分析中...' : '开始分析'}
          </button>
          {datasets.length > 0 ? <span className="muted">参数留空时使用后端默认值。</span> : null}
        </div>
        {datasetsQuery.isError ? (
          <div className="empty" style={{ marginTop: 14 }}>
            <span>
              <AlertTriangle size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
              数据集列表加载失败：{(datasetsQuery.error as Error).message}
            </span>
          </div>
        ) : null}
        {pageError ? (
          <div className="empty" style={{ marginTop: 14 }}>
            <span>
              <AlertTriangle size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
              {pageError}
            </span>
          </div>
        ) : null}
        {analysis.isError ? (
          <div className="empty" style={{ marginTop: 14 }}>
            <span>
              <AlertTriangle size={15} style={{ marginRight: 6, verticalAlign: -2 }} />
              分析失败：{(analysis.error as Error).message}
            </span>
          </div>
        ) : null}
      </div>

      {!result && !analysis.isPending && !pageError && !analysis.isError ? (
        <div className="empty">
          <span>
            <ScanSearch size={18} style={{ display: 'block', margin: '0 auto 8px' }} />
            选择数据集与参数后点击“开始分析”，结果会显示在这里。
          </span>
        </div>
      ) : null}

      {analysis.isPending ? <div className="empty">正在生成价格行为结果...</div> : null}

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
                <div className="stat-label">EMA20</div>
                <div className="stat-value">{fmtNum(result.ema20, 4)}</div>
              </div>
              <span className="stat-icon info"><TrendingUp size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">ATR</div>
                <div className="stat-value">{fmtNum(result.atr, 4)}</div>
                <div className="muted">{fmt100(result.atr_pct)}</div>
              </div>
              <span className="stat-icon warn"><Gauge size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">置信度</div>
                <div className="stat-value">{fmt100(decision.confidence)}</div>
                <div className="muted">{decision.action}</div>
              </div>
              <span className="stat-icon"><ListChecks size={18} /></span>
            </div>
          </div>

          <div className="grid grid-2">
            <div className="panel">
              <div className="section-title">
                <span>市场环境</span>
                <span className="tag">{result.symbol}</span>
              </div>
              <div className="row">
                <span className="badge badge-info">{DIRECTION_LABELS[context.direction] ?? context.direction}</span>
                {context.cycle_position ? <span className="tag">{context.cycle_position}</span> : null}
                {context.recent_spike ? <span className="tag">{context.recent_spike}</span> : null}
                {context.scale_conflict ? <span className="badge badge-warn">多周期方向冲突</span> : null}
              </div>
              <div className="feature-list" style={{ marginTop: 14 }}>
                <div className="feature-item">
                  <div className="label">价格位置</div>
                  <div className="value">{fmtPct(context.price_position)}</div>
                </div>
                <div className="feature-item">
                  <div className="label">观察区间</div>
                  <div className="value">
                    {fmtNum(context.range_low, 4)} ~ {fmtNum(context.range_high, 4)}
                  </div>
                </div>
                <div className="feature-item">
                  <div className="label">背景方向</div>
                  <div className="value">{context.background_direction ?? '--'}</div>
                </div>
                <div className="feature-item">
                  <div className="label">趋势细节</div>
                  <div className="value stack" style={{ gap: 2 }}>
                    <div>短线：{fmtDirectionScore(context.trend_detail?.recent, context.trend_detail?.recent_score)}</div>
                    <div>交易：{fmtDirectionScore(context.trend_detail?.trading, context.trend_detail?.trading_score)}</div>
                    <div>背景：{fmtDirectionScore(context.trend_detail?.background, context.trend_detail?.background_score)}</div>
                  </div>
                </div>
              </div>
            </div>
            <div className="panel">
              <div className="section-title">
                <span>结构特征</span>
                <span className="tag">{result.timeframe}</span>
              </div>
              <div className="row">
                {features.swing_structure ? <span className="tag">{features.swing_structure}</span> : null}
                {features.breakout_quality ? <span className="tag">{features.breakout_quality}</span> : null}
                {(features.patterns ?? []).map((pattern) => (
                  <span key={pattern} className="tag">{pattern}</span>
                ))}
              </div>
              <div className="metric-list" style={{ marginTop: 14 }}>
                <div>
                  <div className="label">支撑</div>
                  <div className="value">{features.supports?.length ?? 0} 个</div>
                </div>
                <div>
                  <div className="label">阻力</div>
                  <div className="value">{features.resistances?.length ?? 0} 个</div>
                </div>
                <div>
                  <div className="label">突破事件</div>
                  <div className="value">{features.breakout_events?.length ?? 0} 次</div>
                </div>
                <div>
                  <div className="label">摆动点</div>
                  <div className="value">{features.swings?.length ?? 0} 个</div>
                </div>
              </div>
              <div className="stack" style={{ gap: 8, marginTop: 14 }}>
                <div className="row">
                  {(features.supports ?? []).map((price, index) => (
                    <span key={`support-${price}-${index}`} className="zone-chip support">
                      {fmtNum(price, 4)}
                    </span>
                  ))}
                  {!features.supports?.length ? <span className="muted">暂无支撑价位</span> : null}
                </div>
                <div className="row">
                  {(features.resistances ?? []).map((price, index) => (
                    <span key={`resistance-${price}-${index}`} className="zone-chip resistance">
                      {fmtNum(price, 4)}
                    </span>
                  ))}
                  {!features.resistances?.length ? <span className="muted">暂无阻力价位</span> : null}
                </div>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">
              <span>行为决策</span>
              <span className="tag">{result.bars_used} 根K线</span>
            </div>
            <div className="row">
              <span className={`badge ${decision.action === 'LONG' ? 'badge-ok' : decision.action === 'SHORT' ? 'badge-danger' : 'badge-neutral'}`}>
                {decision.action === 'LONG' ? '做多观察' : decision.action === 'SHORT' ? '做空观察' : '观望'}
              </span>
              <span className="tag">置信度 {fmt100(decision.confidence)}</span>
              {decision.risk_fraction ? <span className="tag">单笔风险 {fmtPct(decision.risk_fraction)}</span> : null}
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
            {(decision.reason_codes ?? []).length ? (
              <div className="row" style={{ marginTop: 14 }}>
                {(decision.reason_codes ?? []).map((code) => (
                  <span key={code} className="code">{code}</span>
                ))}
              </div>
            ) : null}
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
            <p className="muted" style={{ marginBottom: 0 }}>以上为研究/演示输出，不构成投资建议。</p>
          </div>

          <div className="panel">
            <div className="section-title">
              <span>最近 150 根K线</span>
              <span className="tag">{result.timeframe}</span>
            </div>
            <KlineChart candles={result.candles.slice(-150)} levels={result.levels ?? []} />
          </div>
        </>
      ) : null}
    </div>
  );
}

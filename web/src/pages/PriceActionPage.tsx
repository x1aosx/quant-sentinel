import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { CandlestickChart, Play } from 'lucide-react';
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
      </div>

      <div className="panel">
        <div className="section-title">
          <CandlestickChart size={15} />
          分析参数
        </div>
        <div className="row">
          <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
            {datasets.length === 0 ? <option value="">暂无数据集</option> : null}
            {datasets.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.symbol} · {dataset.timeframe} · {dataset.bar_count} 根
              </option>
            ))}
          </select>
          <label className="muted">lookback</label>
          <input value={lookback} onChange={(e) => setLookback(e.target.value)} placeholder="如 250，可留空" />
          <label className="muted">risk_fraction</label>
          <input value={riskFraction} onChange={(e) => setRiskFraction(e.target.value)} placeholder="如 0.01" />
          <label className="muted">min_rr</label>
          <input value={minRr} onChange={(e) => setMinRr(e.target.value)} placeholder="如 1.5" />
          <select value={stance} onChange={(e) => setStance(e.target.value as 'conservative' | 'balanced' | 'aggressive')}>
            <option value="conservative">保守 conservative</option>
            <option value="balanced">均衡 balanced</option>
            <option value="aggressive">积极 aggressive</option>
          </select>
          <button className="button button-primary" onClick={handleAnalyze} disabled={analysis.isPending || !datasetId}>
            <Play size={14} />
            {analysis.isPending ? '分析中...' : '开始分析'}
          </button>
        </div>
        {datasetsQuery.isError ? <p className="muted">数据集列表加载失败：{(datasetsQuery.error as Error).message}</p> : null}
        {pageError ? <div className="empty">错误：{pageError}</div> : null}
        {analysis.isError ? <div className="empty">分析失败：{(analysis.error as Error).message}</div> : null}
      </div>

      {!result && !analysis.isPending && !pageError && !analysis.isError ? (
        <div className="empty">选择数据集与参数后点击“开始分析”，结果会显示在这里。</div>
      ) : null}

      {result && context && features && decision ? (
        <>
          <div className="grid grid-4">
            <div className="panel stat">
              <div>
                <div className="stat-label">现价</div>
                <div className="stat-value">{fmtNum(result.current_price, 4)}</div>
              </div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">EMA20</div>
                <div className="stat-value">{fmtNum(result.ema20, 4)}</div>
              </div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">ATR</div>
                <div className="stat-value">{fmtNum(result.atr, 4)}</div>
                <div className="muted">{fmtPct(result.atr_pct)}</div>
              </div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">置信度</div>
                <div className="stat-value">{fmtPct(decision.confidence)}</div>
              </div>
            </div>
          </div>

          <div className="grid grid-2">
            <div className="panel">
              <div className="section-title">市场环境</div>
              <div className="row">
                <span className="badge badge-info">{DIRECTION_LABELS[context.direction] ?? context.direction}</span>
                {context.cycle_position ? <span className="tag">{context.cycle_position}</span> : null}
                {context.recent_spike ? <span className="tag">{context.recent_spike}</span> : null}
                {context.scale_conflict ? <span className="badge badge-warn">多周期方向冲突</span> : null}
              </div>
              <p className="muted">
                价格位置 {fmtPct(context.price_position)} · 区间 {fmtNum(context.range_low, 4)} ~ {fmtNum(context.range_high, 4)}
              </p>
              {context.trend_detail ? <div>{context.trend_detail}</div> : null}
              {context.background_direction ? <p className="muted">背景方向：{context.background_direction}</p> : null}
            </div>
            <div className="panel">
              <div className="section-title">结构特征</div>
              <div className="row">
                {features.swing_structure ? <span className="tag">{features.swing_structure}</span> : null}
                {features.breakout_quality ? <span className="tag">{features.breakout_quality}</span> : null}
                {(features.patterns ?? []).map((pattern) => (
                  <span key={pattern} className="tag">{pattern}</span>
                ))}
              </div>
              <p className="muted">
                支撑 {features.supports?.length ?? 0} 个 · 阻力 {features.resistances?.length ?? 0} 个 · 突破事件 {features.breakout_events?.length ?? 0} 次 · 摆动点 {features.swings?.length ?? 0} 个
              </p>
              {features.supports?.length ? <div className="code">支撑: {features.supports.map((price) => fmtNum(price, 4)).join(' / ')}</div> : null}
              {features.resistances?.length ? <div className="code">阻力: {features.resistances.map((price) => fmtNum(price, 4)).join(' / ')}</div> : null}
            </div>
          </div>

          <div className="panel">
            <div className="section-title">行为决策</div>
            <div className="row">
              <span className={`badge ${decision.action === 'LONG' ? 'badge-ok' : decision.action === 'SHORT' ? 'badge-danger' : 'badge-neutral'}`}>
                {decision.action === 'LONG' ? '做多观察' : decision.action === 'SHORT' ? '做空观察' : '观望'}
              </span>
              <span className="tag">置信度 {fmtPct(decision.confidence)}</span>
              {decision.risk_fraction ? <span className="tag">单笔风险 {fmtPct(decision.risk_fraction)}</span> : null}
            </div>
            <div className="row">
              <span className="muted">entry {fmtNum(decision.entry, 4)}</span>
              <span className="muted">stop {fmtNum(decision.stop, 4)}</span>
              <span className="muted">target {fmtNum(decision.target, 4)}</span>
              <span className="muted">rr {fmtNum(decision.rr)}</span>
            </div>
            {(decision.reason_codes ?? []).length ? (
              <div className="row">
                {(decision.reason_codes ?? []).map((code) => (
                  <span key={code} className="code">{code}</span>
                ))}
              </div>
            ) : null}
            {decision.reasoning ? <p>{decision.reasoning}</p> : null}
            {decision.invalidation ? <p className="muted">失效条件：{decision.invalidation}</p> : null}
            <p className="muted">以上为研究/演示输出，不构成投资建议。</p>
          </div>

          <div className="panel">
            <div className="section-title">最近 150 根K线</div>
            <KlineChart candles={result.candles.slice(-150)} levels={result.levels ?? []} />
          </div>
        </>
      ) : null}
    </div>
  );
}

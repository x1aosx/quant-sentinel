import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  CircleDollarSign,
  Gauge,
  Layers,
  Loader2,
  Play,
  ScanSearch,
  ShieldCheck,
} from 'lucide-react';
import { KlineChart } from '../components/KlineChart';
import type { DatasetSummary, SrAnalysisResult } from '../types';

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

function fmt100(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return `${value.toFixed(digits)}%`;
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

export function SupportResistancePage() {
  const [datasetId, setDatasetId] = useState('');
  const [lookback, setLookback] = useState('250');
  const [nZones, setNZones] = useState('8');
  const [direction, setDirection] = useState<'both' | 'long' | 'short'>('both');
  const [pageError, setPageError] = useState('');

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: listDatasets });
  const datasets = datasetsQuery.data?.items ?? [];

  useEffect(() => {
    if (!datasetId && datasets.length) {
      setDatasetId(datasets[0].id);
    }
  }, [datasetId, datasets]);

  const analysis = useMutation({
    mutationFn: (payload: { dataset_id: string; lookback?: number; n_zones?: number; direction?: 'both' | 'long' | 'short' }) =>
      requestJson<SrAnalysisResult>('/analysis/support-resistance', { method: 'POST', body: JSON.stringify(payload) }),
  });

  const handleAnalyze = () => {
    setPageError('');
    if (!datasetId) {
      setPageError('请先选择数据集（可先到数据中心导入）。');
      return;
    }
    try {
      const parsedLookback = parseOptionalInt(lookback, 'lookback');
      const parsedNZones = parseOptionalInt(nZones, 'n_zones');
      analysis.mutate({
        dataset_id: datasetId,
        ...(parsedLookback !== undefined ? { lookback: parsedLookback } : {}),
        ...(parsedNZones !== undefined ? { n_zones: parsedNZones } : {}),
        direction,
      });
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    }
  };

  const result = analysis.data;
  const riskReward = result?.summary.risk_reward;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>支撑阻力分析</h1>
          <div className="muted">基于所选数据集识别关键价位区间，结果仅用于研究参考。</div>
        </div>
        {result ? (
          <div className="row">
            <span className="badge badge-info">{result.symbol}</span>
            <span className="tag">{result.timeframe}</span>
            <span className="tag">{result.levels.length} 个区间</span>
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="row">
            <Layers size={15} />
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
                  {dataset.title || dataset.symbol} · {dataset.symbol} · {dataset.timeframe} ·{' '}
                  {dataset.bar_count} 根
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Lookback</span>
            <input value={lookback} onChange={(e) => setLookback(e.target.value)} placeholder="如 250，可留空" />
          </label>
          <label className="field">
            <span>区间数量</span>
            <input value={nZones} onChange={(e) => setNZones(e.target.value)} placeholder="如 8，可留空" />
          </label>
          <label className="field">
            <span>方向</span>
            <select value={direction} onChange={(e) => setDirection(e.target.value as 'both' | 'long' | 'short')}>
              <option value="both">双向 both</option>
              <option value="long">仅做多 long</option>
              <option value="short">仅做空 short</option>
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

      {analysis.isPending ? <div className="empty">正在识别支撑阻力区间...</div> : null}

      {result ? (
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
                <div className="stat-label">ATR</div>
                <div className="stat-value">{fmtNum(result.atr, 4)}</div>
                <div className="muted">{fmt100(result.atr_pct)}</div>
              </div>
              <span className="stat-icon warn"><Gauge size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">趋势</div>
                <div className="stat-value">{result.trend.label}</div>
                {result.trend.detail ? <div className="muted">{result.trend.detail}</div> : null}
              </div>
              <span className="stat-icon info"><ShieldCheck size={18} /></span>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">风险回报</div>
                <div className="stat-value">{fmtNum(riskReward?.risk_reward_ratio)}</div>
                <div className="muted">{riskReward?.quality ?? '--'}</div>
              </div>
              <span className="stat-icon"><Layers size={18} /></span>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">
              <span>概览</span>
              <span className="tag">{result.bars_used} 根K线</span>
            </div>
            {result.summary.headline ? <div className="feature-item"><div className="value">{result.summary.headline}</div></div> : <div className="empty">后端未返回 headline。</div>}
            {riskReward ? (
              <div className="metric-list" style={{ marginTop: 14 }}>
                <div>
                  <div className="label">风险回报比</div>
                  <div className="value">{fmtNum(riskReward.risk_reward_ratio)}</div>
                </div>
                <div>
                  <div className="label">质量</div>
                  <div className="value">{riskReward.quality ?? '--'}</div>
                </div>
                <div>
                  <div className="label">潜在收益</div>
                  <div className="value">{fmt100(riskReward.potential_profit_pct)}</div>
                </div>
                <div>
                  <div className="label">潜在损失</div>
                  <div className="value">{fmt100(riskReward.potential_loss_pct)}</div>
                </div>
              </div>
            ) : null}
            {result.summary.caveat ? <p className="muted" style={{ marginBottom: 0 }}>提示：{result.summary.caveat}</p> : null}
          </div>

          <div className="panel">
            <div className="section-title">
              <span>价位区间</span>
              <span className="tag">{result.levels.length} 个</span>
            </div>
            {result.levels.length === 0 ? (
              <div className="empty">后端未识别出价位区间。</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>Center</th>
                    <th>Low</th>
                    <th>High</th>
                    <th>Distance %</th>
                    <th>Distance ATR</th>
                    <th>Width ATR</th>
                    <th>Events</th>
                    <th>Volume %</th>
                    <th>Edge Score</th>
                    <th>TF Count / TFS</th>
                  </tr>
                </thead>
                <tbody>
                  {result.levels.map((level, index) => (
                    <tr key={`${level.zone_type}-${level.center}-${index}`}>
                      <td>
                        <span className={`zone-chip ${level.zone_type}`}>{level.zone_type === 'support' ? '支撑' : '阻力'}</span>
                      </td>
                      <td>{fmtNum(level.center, 4)}</td>
                      <td>{fmtNum(level.low, 4)}</td>
                      <td>{fmtNum(level.high, 4)}</td>
                      <td>{fmt100(level.distance_pct)}</td>
                      <td>{fmtNum(level.distance_atr)}</td>
                      <td>{fmtNum(level.width_atr)}</td>
                      <td>{fmtNum(level.n_events ?? level.touch_count, 0)}</td>
                      <td>{fmt100(level.volume_pct)}</td>
                      <td>{fmtNum(level.edge_score)}</td>
                      <td className="muted">{level.tf_count ?? '--'}{level.tfs ? ` · ${level.tfs}` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="panel">
            <div className="section-title">
              <span>K线与价位区间</span>
              <span className="tag">{result.symbol}</span>
            </div>
            <KlineChart candles={result.candles.slice(-150)} levels={result.levels} />
          </div>
        </>
      ) : null}
    </div>
  );
}

import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Layers, Play } from 'lucide-react';
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
      </div>

      <div className="panel">
        <div className="section-title">
          <Layers size={15} />
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
          <label className="muted">n_zones</label>
          <input value={nZones} onChange={(e) => setNZones(e.target.value)} placeholder="如 8，可留空" />
          <select value={direction} onChange={(e) => setDirection(e.target.value as 'both' | 'long' | 'short')}>
            <option value="both">双向 both</option>
            <option value="long">仅做多 long</option>
            <option value="short">仅做空 short</option>
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

      {result ? (
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
                <div className="stat-label">ATR</div>
                <div className="stat-value">{fmtNum(result.atr, 4)}</div>
                <div className="muted">{fmt100(result.atr_pct)}</div>
              </div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">趋势</div>
                <div className="stat-value">{result.trend.label}</div>
                {result.trend.detail ? <div className="muted">{result.trend.detail}</div> : null}
              </div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">风险回报</div>
                <div className="stat-value">{fmtNum(riskReward?.risk_reward_ratio)}</div>
                <div className="muted">{riskReward?.quality ?? '--'}</div>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">概览</div>
            {result.summary.headline ? <div>{result.summary.headline}</div> : <div className="empty">后端未返回 headline。</div>}
            {riskReward ? (
              <p className="muted">
                潜在收益 {fmt100(riskReward.potential_profit_pct)} · 潜在损失 {fmt100(riskReward.potential_loss_pct)}
              </p>
            ) : null}
            {result.summary.caveat ? <p className="muted">提示：{result.summary.caveat}</p> : null}
            <p className="muted">
              使用 {result.bars_used} 根K线 · {result.symbol} · {result.timeframe}
            </p>
          </div>

          <div className="panel">
            <div className="section-title">价位区间</div>
            {result.levels.length === 0 ? (
              <div className="empty">后端未识别出价位区间。</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>center</th>
                    <th>low</th>
                    <th>high</th>
                    <th>distance_pct</th>
                    <th>distance_atr</th>
                    <th>width_atr</th>
                    <th>n_events</th>
                    <th>volume_pct</th>
                    <th>edge_score</th>
                    <th>tf_count/tfs</th>
                  </tr>
                </thead>
                <tbody>
                  {result.levels.map((level, index) => (
                    <tr key={`${level.zone_type}-${level.center}-${index}`}>
                      <td>{level.zone_type === 'support' ? '支撑' : '阻力'}</td>
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
            <div className="section-title">K线与价位区间</div>
            <KlineChart candles={result.candles.slice(-150)} levels={result.levels} />
          </div>
        </>
      ) : null}
    </div>
  );
}

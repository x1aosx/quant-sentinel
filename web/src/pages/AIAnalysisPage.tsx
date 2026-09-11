import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Bell,
  Bot,
  CalendarClock,
  CheckCircle2,
  Download,
  Eye,
  Gauge,
  ListTree,
  MonitorUp,
  PlayCircle,
  Plus,
  RefreshCcw,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  TrendingUp,
  WandSparkles,
} from 'lucide-react';
import { api, streamAIAnalysis } from '../api/client';
import { TradingViewExchangeSelect } from '../components/TradingViewExchangeSelect';
import { DecisionVisualization } from '../components/ai/DecisionVisualization';
import { KlineChart } from '../components/KlineChart';
import type {
  AIAnalysisRecord,
  BatchAnalyzeResponse,
  DatasetSummary,
  MonitorTarget,
  SrLevel,
} from '../types';
import {
  actionOf,
  confidenceOf,
  decisionOf,
  diagnosisOf,
  formatValue,
  probabilityRows,
  responseText,
} from '../utils/aiRecord';

const VIEWS = [
  { key: 'live', label: '实时分析', icon: Activity },
  { key: 'diagnosis', label: '诊断', icon: Eye },
  { key: 'decision', label: '决策', icon: Gauge },
  { key: 'visualization', label: '决策可视化', icon: ListTree },
  { key: 'future', label: '未来走势', icon: TrendingUp },
  { key: 'raw', label: '原始', icon: Bot },
  { key: 'debug', label: '调试', icon: ShieldAlert },
] as const;

type ViewKey = (typeof VIEWS)[number]['key'];

const NEW_TARGET: MonitorTarget = {
  symbol: '',
  timeframe: '15m',
  source: 'yfinance',
  enabled: true,
  analysis: {
    analysis_bar_count: 120,
    decision_stance: 'balanced',
    enable_next_bar_prediction: true,
  },
};

function confidenceBadge(value: number) {
  if (value >= 70) return 'badge badge-ok';
  if (value >= 40) return 'badge badge-warn';
  return 'badge badge-neutral';
}

function decisionBadge(action: string) {
  if (action === 'LONG') return 'badge badge-ok';
  if (action === 'SHORT') return 'badge badge-danger';
  return 'badge badge-neutral';
}

export function AIAnalysisPage() {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'single' | 'monitor'>('single');
  const [view, setView] = useState<ViewKey>('live');
  const [datasetId, setDatasetId] = useState('');
  const [importSymbol, setImportSymbol] = useState('GC=F');
  const [importTimeframe, setImportTimeframe] = useState('1d');
  const [importSource, setImportSource] = useState<
    'yfinance' | 'akshare' | 'tradingview' | 'mt5'
  >('yfinance');
  const [importExchange, setImportExchange] = useState('');
  const [record, setRecord] = useState<AIAnalysisRecord | null>(null);
  const [streamLog, setStreamLog] = useState('');
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [watchlist, setWatchlist] = useState<MonitorTarget[]>([]);
  const [selectedMonitor, setSelectedMonitor] = useState<number | null>(null);
  const [lastBatch, setLastBatch] = useState<BatchAnalyzeResponse | null>(null);

  const configQuery = useQuery({ queryKey: ['system-config'], queryFn: api.getSystemConfig });
  const datasetQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const monitorQuery = useQuery({
    queryKey: ['monitor-status'],
    queryFn: api.getMonitorStatus,
    refetchInterval: 4000,
  });
  const datasets: DatasetSummary[] = datasetQuery.data?.items ?? [];
  const config = configQuery.data;

  useEffect(() => {
    if (!datasetId && datasets.length) setDatasetId(datasets[0].id);
  }, [datasetId, datasets]);

  useEffect(() => {
    if (config && watchlist.length === 0) {
      setWatchlist(config.monitor_watchlist.map((target) => ({ ...target })));
    }
  }, [config, watchlist.length]);

  const importRemote = useMutation({
    mutationFn: () =>
      api.importRemoteDataset({
        source: importSource,
        symbol: importSymbol,
        timeframe: importTimeframe,
        lookback: 500,
        exchange: importSource === 'tradingview' ? importExchange : undefined,
      }),
    onSuccess: (created) => {
      setDatasetId(created.id);
      setNotice(`行情已导入：${created.symbol} ${created.timeframe}`);
      setError('');
      void queryClient.invalidateQueries({ queryKey: ['datasets'] });
    },
    onError: (reason: Error) => setError(reason.message),
  });

  const runAnalysis = async () => {
    if (!datasetId) return;
    setRunning(true);
    setRecord(null);
    setStreamLog('正在连接分析服务...\n');
    setError('');
    setNotice('');
    setMode('single');
    setView('live');
    try {
      const result = await streamAIAnalysis({ dataset_id: datasetId }, (event) => {
        if (event.type === 'snapshot') {
          setStreamLog((current) => `${current}快照 ${event.symbol} ${event.timeframe} ${event.bar_count} 根\n`);
        } else if (
          ['log', 'stage1', 'stage1_reasoning', 'stage2', 'stage2_reasoning'].includes(event.type)
        ) {
          const labels: Record<string, string> = {
            log: '系统',
            stage1: '阶段一正文',
            stage1_reasoning: '阶段一推理',
            stage2: '阶段二正文',
            stage2_reasoning: '阶段二推理',
          };
          setStreamLog((current) => `${current}[${labels[event.type] ?? event.type}] ${event.text ?? ''}`);
        } else if (event.type === 'error') {
          const message = event.message ?? '分析失败';
          setError(message);
          setStreamLog((current) => `${current}[错误] ${message}\n`);
        } else if (event.type === 'done') {
          setStreamLog((current) => `${current}[系统] 分析流程已结束。\n`);
        }
      });
      setRecord(result);
      setNotice(result.status === 'ok' ? '分析完成' : '分析返回异常记录');
      if (result.status !== 'ok') setView('debug');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRunning(false);
    }
  };

  const followup = useMutation({
    mutationFn: () => api.followupAI({ record, question }),
    onSuccess: () => setError(''),
    onError: (reason: Error) => setError(reason.message),
  });

  const notify = useMutation({
    mutationFn: () => api.sendFeishu({ record }),
    onSuccess: (result) =>
      setNotice(result.sent ? '飞书通知已发送' : `飞书通知未发送：${result.reason ?? '未知原因'}`),
    onError: (reason: Error) => setError(reason.message),
  });

  const startMonitor = useMutation({
    mutationFn: () =>
      api.startMonitor({
        targets: watchlist.filter((target) => target.enabled),
        interval_seconds: config?.analysis.monitor_interval_seconds ?? 60,
        auto_notify: config?.feishu.enabled ?? false,
      }),
    onSuccess: () => {
      setNotice('实时盯盘已启动');
      void queryClient.invalidateQueries({ queryKey: ['monitor-status'] });
    },
    onError: (reason: Error) => setError(reason.message),
  });

  const stopMonitor = useMutation({
    mutationFn: api.stopMonitor,
    onSuccess: () => {
      setNotice('实时盯盘已停止');
      void queryClient.invalidateQueries({ queryKey: ['monitor-status'] });
    },
    onError: (reason: Error) => setError(reason.message),
  });

  const runMonitorOnce = useMutation({
    mutationFn: async () => {
      const status = monitorQuery.data;
      if (!status?.running) {
        await api.startMonitor({
          targets: watchlist.filter((target) => target.enabled),
          interval_seconds: config?.analysis.monitor_interval_seconds ?? 60,
          auto_notify: false,
        });
      }
      return api.runMonitorOnce();
    },
    onSuccess: () => {
      setNotice('已完成一次盯盘检查');
      void queryClient.invalidateQueries({ queryKey: ['monitor-status'] });
    },
    onError: (reason: Error) => setError(reason.message),
  });

  const batchAnalysis = useMutation({
    mutationFn: () =>
      api.batchAnalyze({
        targets: watchlist.filter((target) => target.enabled),
        concurrency: config?.analysis.concurrency ?? 3,
      }),
    onSuccess: (result) => {
      setLastBatch(result);
      const completed = result.items.find((item) => item.record);
      if (completed?.record) setRecord(completed.record);
      setNotice(`批量分析完成：${result.summary.succeeded}/${result.summary.total}`);
      setMode('monitor');
    },
    onError: (reason: Error) => setError(reason.message),
  });

  const diagnosis = diagnosisOf(record);
  const decision = decisionOf(record);
  const action = actionOf(record);
  const confidence = confidenceOf(record);
  const snapshot = record?.snapshot as Record<string, any> | undefined;
  const supports = (snapshot?.support_resistance?.supports_only ?? []) as SrLevel[];
  const resistances = (snapshot?.support_resistance?.resistances ?? []) as SrLevel[];
  const monitorItems = monitorQuery.data?.items ?? [];
  const selectedMonitorItem =
    selectedMonitor !== null ? monitorItems[selectedMonitor] : monitorItems[0] ?? null;
  const nextBar = (record?.next_bar_prediction ??
    record?.stage2_decision?.next_bar_prediction ??
    {}) as Record<string, any>;
  const nextCycle = (record?.next_cycle_prediction ??
    record?.stage2_decision?.next_cycle_prediction ??
    {}) as Record<string, any>;
  const futureTrend = (record?.future_trend ??
    record?.stage2_decision?.future_trend ??
    {}) as Record<string, any>;
  const nextBarProbabilities = useMemo(
    () => probabilityRows(nextBar.probabilities),
    [nextBar.probabilities],
  );
  const nextCycleProbabilities = useMemo(
    () => probabilityRows(nextCycle.probabilities),
    [nextCycle.probabilities],
  );

  const updateTarget = (index: number, patch: Partial<MonitorTarget>) => {
    setWatchlist((current) =>
      current.map((target, targetIndex) =>
        targetIndex === index ? { ...target, ...patch } : target,
      ),
    );
  };

  const chooseMonitorRecord = (index: number) => {
    setSelectedMonitor(index);
    const item = monitorItems[index];
    if (item?.last_record) {
      setRecord(item.last_record);
      setMode('monitor');
      setView('decision');
    }
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>AI 分析中心</h1>
          <div className="muted">
            两阶段行情诊断、决策路径、未来预期与多标的实时盯盘。所有结果仅用于研究模拟。
          </div>
        </div>
        <div className="row">
          <span className="tag">{datasets.length} 个数据集</span>
          <span className={config?.provider.api_key_configured ? 'badge badge-ok' : 'badge badge-warn'}>
            {config?.provider.api_key_configured ? '模型已配置' : '本地研究模式'}
          </span>
        </div>
      </div>

      {error ? <div className="empty">错误：{error}</div> : null}
      {notice ? <div className="notice">{notice}</div> : null}

      <div className="mode-switch">
        <button
          className={mode === 'single' ? 'mode-card active' : 'mode-card'}
          onClick={() => setMode('single')}
        >
          <Sparkles size={18} />
          <span>
            <strong>单次分析</strong>
            <small>选择数据集执行完整两阶段分析</small>
          </span>
        </button>
        <button
          className={mode === 'monitor' ? 'mode-card active' : 'mode-card'}
          onClick={() => setMode('monitor')}
        >
          <MonitorUp size={18} />
          <span>
            <strong>实时盯盘</strong>
            <small>多标的回调检测、批量分析与飞书通知</small>
          </span>
        </button>
      </div>

      {mode === 'single' ? (
        <div className="grid grid-2">
          <div className="panel">
            <div className="section-title">
              <Download size={15} />
              行情准备
            </div>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ai-source">数据源</label>
                <select
                  id="ai-source"
                  value={importSource}
                  onChange={(event) =>
                    setImportSource(
                      event.target.value as 'yfinance' | 'akshare' | 'tradingview' | 'mt5',
                    )
                  }
                >
                  <option value="yfinance">YFinance</option>
                  <option value="akshare">AkShare / A股</option>
                  <option value="tradingview">TradingView</option>
                  <option value="mt5">MT5</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="ai-symbol">标的代码</label>
                <input
                  id="ai-symbol"
                  value={importSymbol}
                  onChange={(event) => setImportSymbol(event.target.value)}
                  placeholder="GC=F、EURUSD=X 或 600519"
                />
              </div>
              {importSource === 'tradingview' ? (
                <div className="field">
                  <label htmlFor="ai-exchange">交易所</label>
                  <TradingViewExchangeSelect
                    id="ai-exchange"
                    value={importExchange}
                    onChange={setImportExchange}
                  />
                </div>
              ) : null}
              <div className="field">
                <label htmlFor="ai-timeframe">周期</label>
                <select
                  id="ai-timeframe"
                  value={importTimeframe}
                  onChange={(event) => setImportTimeframe(event.target.value)}
                >
                  {['1m', '5m', '15m', '30m', '1h', '1d', '1w'].map((timeframe) => (
                    <option key={timeframe} value={timeframe}>
                      {timeframe}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button className="button" onClick={() => importRemote.mutate()} disabled={importRemote.isPending}>
                <Download size={14} />
                {importRemote.isPending ? '下载中...' : '下载行情'}
              </button>
              <span className="muted">导入 500 根公开行情并更新数据集。</span>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">
              <WandSparkles size={15} />
              分析执行
              <span className="tag">两阶段</span>
            </div>
            <div className="field">
              <label htmlFor="ai-dataset">分析数据集</label>
              <select id="ai-dataset" value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
                {datasets.length === 0 ? <option value="">暂无数据集</option> : null}
                {datasets.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {dataset.symbol} · {dataset.timeframe} · {dataset.bar_count} 根
                  </option>
                ))}
              </select>
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button
                className="button button-primary"
                onClick={() => void runAnalysis()}
                disabled={running || !datasetId}
              >
                <PlayCircle size={14} />
                {running ? '分析中...' : '开始两阶段分析'}
              </button>
              {record ? (
                <button className="button" onClick={() => notify.mutate()} disabled={notify.isPending}>
                  <Bell size={14} />
                  {notify.isPending ? '发送中...' : '发送飞书通知'}
                </button>
              ) : null}
            </div>
            <p className="muted" style={{ marginBottom: 0 }}>
              模型、代理与飞书凭据在系统配置页统一维护。
            </p>
          </div>
        </div>
      ) : (
        <div className="panel">
          <div className="section-title">
            <MonitorUp size={15} />
            实时盯盘
            <span className={monitorQuery.data?.running ? 'badge badge-ok' : 'badge badge-neutral'}>
              {monitorQuery.data?.running ? '运行中' : '已停止'}
            </span>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>启用</th>
                  <th>数据源</th>
                  <th>标的</th>
                  <th>周期</th>
                  <th>分析 K 线</th>
                  <th>状态</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {watchlist.length === 0 ? (
                  <tr>
                    <td colSpan={7}>
                      <div className="empty">添加股票、外汇、期货或加密标的开始盯盘。</div>
                    </td>
                  </tr>
                ) : (
                  watchlist.map((target, index) => {
                    const status = monitorItems[index];
                    const itemRecord = status?.last_record;
                    const itemAction = actionOf(itemRecord);
                    return (
                      <tr
                        key={`${target.source}-${target.symbol}-${target.timeframe}-${index}`}
                        onClick={() => chooseMonitorRecord(index)}
                      >
                        <td>
                          <input
                            type="checkbox"
                            checked={target.enabled}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) => updateTarget(index, { enabled: event.target.checked })}
                          />
                        </td>
                        <td>
                          <select
                            value={target.source}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) => updateTarget(index, { source: event.target.value })}
                          >
                            <option value="yfinance">YFinance</option>
                            <option value="akshare">AkShare</option>
                            <option value="tradingview">TradingView</option>
                            <option value="mt5">MT5</option>
                          </select>
                          {target.source === 'tradingview' ? (
                            <TradingViewExchangeSelect
                              value={target.exchange ?? ''}
                              onChange={(value) => updateTarget(index, { exchange: value })}
                              ariaLabel="交易所"
                              onClick={(event) => event.stopPropagation()}
                            />
                          ) : null}
                        </td>
                        <td>
                          <input
                            value={target.symbol}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) => updateTarget(index, { symbol: event.target.value })}
                            placeholder="XAUUSD / 600519"
                          />
                        </td>
                        <td>
                          <select
                            value={target.timeframe}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) => updateTarget(index, { timeframe: event.target.value })}
                          >
                            {['1m', '5m', '15m', '30m', '1h', '1d', '1w'].map((timeframe) => (
                              <option key={timeframe} value={timeframe}>
                                {timeframe}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <input
                            type="number"
                            min="60"
                            max="1000"
                            value={target.analysis?.analysis_bar_count ?? 120}
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) =>
                              updateTarget(index, {
                                analysis: {
                                  ...target.analysis,
                                  analysis_bar_count: Number(event.target.value) || 120,
                                },
                              })
                            }
                          />
                        </td>
                        <td>
                          <div className="stack compact">
                            <span className={status?.status === 'error' ? 'badge badge-danger' : 'badge badge-neutral'}>
                              {status?.status ?? '未运行'}
                            </span>
                            <span className="muted">
                              {status?.last_session ?? '等待数据'} · {status?.run_count ?? 0} 次
                            </span>
                            {itemRecord ? (
                              <span className={decisionBadge(itemAction)}>
                                {itemAction} {confidenceOf(itemRecord)}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td>
                          <button
                            className="button button-danger"
                            onClick={(event) => {
                              event.stopPropagation();
                              setWatchlist((current) =>
                                current.filter((_, targetIndex) => targetIndex !== index),
                              );
                            }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="button" onClick={() => setWatchlist((current) => [...current, { ...NEW_TARGET }])}>
              <Plus size={14} />
              添加标的
            </button>
            <button
              className="button"
              onClick={() => void batchAnalysis.mutate()}
              disabled={batchAnalysis.isPending || watchlist.length === 0}
            >
              <WandSparkles size={14} />
              {batchAnalysis.isPending ? '批量分析中...' : '立即批量分析'}
            </button>
            {monitorQuery.data?.running ? (
              <button className="button button-danger" onClick={() => stopMonitor.mutate()}>
                <Square size={14} />
                停止盯盘
              </button>
            ) : (
              <button
                className="button button-primary"
                onClick={() => startMonitor.mutate()}
                disabled={startMonitor.isPending}
              >
                <PlayCircle size={14} />
                启动盯盘
              </button>
            )}
            <button className="button" onClick={() => runMonitorOnce.mutate()} disabled={runMonitorOnce.isPending}>
              <RefreshCcw size={14} />
              {runMonitorOnce.isPending ? '检查中...' : '立即检查'}
            </button>
          </div>
          {lastBatch ? (
            <div className="batch-summary">
              <span>完成 {lastBatch.summary.succeeded ?? 0}</span>
              <span>失败 {lastBatch.summary.failed ?? 0}</span>
              <span>耗时 {formatValue(lastBatch.summary.duration_ms)} ms</span>
            </div>
          ) : null}
        </div>
      )}

      <div className="panel">
        <div className="section-title">
          <ListTree size={15} />
          分析结果
          {record ? <span className="tag">{record.symbol ?? selectedMonitorItem?.target.symbol}</span> : null}
        </div>
        <div className="segmented segmented-scroll">
          {VIEWS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={view === item.key ? 'button button-primary' : 'button'}
                onClick={() => setView(item.key)}
              >
                <Icon size={14} />
                {item.label}
              </button>
            );
          })}
        </div>

        {!record && view !== 'live' ? (
          <div className="empty">选择数据集执行分析，或从盯盘列表打开最近结果。</div>
        ) : null}

        {view === 'live' ? (
          <div className="grid grid-2" style={{ marginTop: 16 }}>
            <div>
              <div className="section-title">
                <Activity size={15} />
                事件流
              </div>
              <pre className="raw-prompt stream-log">{streamLog || '等待分析任务...'}</pre>
            </div>
            <div>
              <div className="section-title">
                <CheckCircle2 size={15} />
                当前状态
              </div>
              <div className="metric-list">
                <div>
                  <div className="label">运行</div>
                  <div className="value">{running ? '分析中' : record ? '已完成' : '待执行'}</div>
                </div>
                <div>
                  <div className="label">决策</div>
                  <div className="value">{record ? action : '--'}</div>
                </div>
                <div>
                  <div className="label">置信度</div>
                  <div className="value">{record ? confidence : '--'}</div>
                </div>
                <div>
                  <div className="label">耗时</div>
                  <div className="value">{record?.duration_ms ? `${record.duration_ms} ms` : '--'}</div>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {view === 'diagnosis' && record ? (
          <div className="stack" style={{ marginTop: 16 }}>
            <div className="grid grid-4">
              <div className="panel stat">
                <div>
                  <div className="stat-label">趋势方向</div>
                  <div className="stat-value">{diagnosis.current_trend?.direction ?? '--'}</div>
                </div>
                <TrendingUp size={18} />
              </div>
              <div className="panel stat">
                <div>
                  <div className="stat-label">当前周期</div>
                  <div className="stat-value">{diagnosis.current_cycle ?? '--'}</div>
                </div>
                <CalendarClock size={18} />
              </div>
              <div className="panel stat">
                <div>
                  <div className="stat-label">下一周期</div>
                  <div className="stat-value">{diagnosis.next_cycle ?? '--'}</div>
                </div>
                <RefreshCcw size={18} />
              </div>
              <div className="panel stat">
                <div>
                  <div className="stat-label">诊断置信度</div>
                  <div className="stat-value">{formatValue(diagnosis.confidence)}</div>
                </div>
                <Gauge size={18} />
              </div>
            </div>
            <div className="grid grid-2">
              <div className="panel">
                <div className="section-title">诊断摘要</div>
                <p>{diagnosis.diagnosis_summary ?? '未提供'}</p>
                <div className="feature-list">
                  {(diagnosis.key_factors ?? []).map((factor: unknown, index: number) => (
                    <div key={`${String(factor)}-${index}`} className="feature-item">
                      <div className="label">{String(factor)}</div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="panel">
                <div className="section-title">K线与关键区</div>
                <KlineChart
                  candles={(snapshot?.candles ?? []).slice(-150)}
                  levels={[...supports, ...resistances]}
                />
                <div className="row">
                  <span className="zone-chip support">支撑 {supports.length}</span>
                  <span className="zone-chip resistance">阻力 {resistances.length}</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {view === 'decision' && record ? (
          <div className="grid grid-2" style={{ marginTop: 16 }}>
            <div className="panel highlight-card">
              <div className="section-title">
                <Gauge size={15} />
                交易决策
                <span className={decisionBadge(action)}>{action}</span>
              </div>
              <div className="metric-list">
                <div>
                  <div className="label">置信度</div>
                  <div className="value">{confidence}</div>
                </div>
                <div>
                  <div className="label">Entry</div>
                  <div className="value">{formatValue(decision.entry)}</div>
                </div>
                <div>
                  <div className="label">Stop</div>
                  <div className="value">{formatValue(decision.stop)}</div>
                </div>
                <div>
                  <div className="label">Target</div>
                  <div className="value">{formatValue(decision.target)}</div>
                </div>
                <div>
                  <div className="label">RR</div>
                  <div className="value">{formatValue(decision.rr)}</div>
                </div>
              </div>
              <p>{decision.reasoning ?? '未提供决策理由。'}</p>
            </div>
            <div className="panel">
              <div className="section-title">风险与失效条件</div>
              <div className="feature-list">
                <div className="feature-item">
                  <div className="label">失效条件</div>
                  <div className="value">{formatValue(decision.invalidation)}</div>
                </div>
                <div className="feature-item">
                  <div className="label">关注点</div>
                  <div className="value">
                    {(decision.watch_points ?? []).length
                      ? (decision.watch_points as unknown[]).join('、')
                      : '--'}
                  </div>
                </div>
                <div className="feature-item">
                  <div className="label">风险</div>
                  <div className="value">
                    {(decision.risk_flags ?? []).length
                      ? (decision.risk_flags as unknown[]).join('、')
                      : '--'}
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {view === 'visualization' && record ? (
          <div style={{ marginTop: 16 }}>
            <DecisionVisualization record={record} />
          </div>
        ) : null}

        {view === 'future' && record ? (
          <div className="grid grid-2" style={{ marginTop: 16 }}>
            <div className="panel">
              <div className="section-title">未来走势</div>
              <div className="feature-list">
                <div className="feature-item">
                  <div className="label">方向</div>
                  <div className="value">{futureTrend.label ?? futureTrend.direction ?? '--'}</div>
                </div>
                <div className="feature-item">
                  <div className="label">置信度</div>
                  <div className="value">{formatValue(futureTrend.confidence)}</div>
                </div>
                <div className="feature-item">
                  <div className="label">下一周期</div>
                  <div className="value">{nextCycle.cycle ?? nextCycle.label ?? '--'}</div>
                </div>
                <div className="feature-item">
                  <div className="label">下一根 K 线</div>
                  <div className="value">{nextBar.direction ?? nextBar.label ?? '--'}</div>
                </div>
              </div>
              <p>{futureTrend.reasoning ?? nextBar.reasoning ?? '未提供预测说明。'}</p>
            </div>
            <div className="panel">
              <div className="section-title">概率分布</div>
              {nextBarProbabilities.length === 0 && nextCycleProbabilities.length === 0 ? (
                <div className="empty">当前记录没有概率分布。</div>
              ) : (
                <div className="probability-list">
                  {(nextBarProbabilities.length ? nextBarProbabilities : nextCycleProbabilities).map(
                    (item) => (
                      <div key={item.label} className="probability-row">
                        <span>{item.label}</span>
                        <div className="progress">
                          <div
                            className="progress-fill"
                            style={{ width: `${Math.max(0, Math.min(100, item.probability))}%` }}
                          />
                        </div>
                        <strong>{item.probability}%</strong>
                      </div>
                    ),
                  )}
                </div>
              )}
            </div>
          </div>
        ) : null}

        {view === 'raw' && record ? (
          <div className="stack" style={{ marginTop: 16 }}>
            <div className="grid grid-2">
              <div>
                <strong>阶段一 Prompt</strong>
                <pre className="raw-prompt">{JSON.stringify(record.raw_prompt?.stage1 ?? [], null, 2)}</pre>
              </div>
              <div>
                <strong>阶段二 Prompt</strong>
                <pre className="raw-prompt">{JSON.stringify(record.raw_prompt?.stage2 ?? [], null, 2)}</pre>
              </div>
            </div>
            <div className="grid grid-2">
              <div>
                <strong>阶段一响应</strong>
                <pre className="raw-prompt">{responseText(record.stage1_response) || record.stage1_response_text}</pre>
              </div>
              <div>
                <strong>阶段二响应</strong>
                <pre className="raw-prompt">{responseText(record.stage2_response) || record.stage2_response_text}</pre>
              </div>
            </div>
          </div>
        ) : null}

        {view === 'debug' && record ? (
          <div className="grid grid-2" style={{ marginTop: 16 }}>
            <div className="panel">
              <div className="section-title">运行信息</div>
              <div className="metric-list">
                <div>
                  <div className="label">状态</div>
                  <div className="value">{record.status}</div>
                </div>
                <div>
                  <div className="label">耗时</div>
                  <div className="value">{record.duration_ms ?? '--'} ms</div>
                </div>
                <div>
                  <div className="label">模型</div>
                  <div className="value">{record.debug?.provider?.model ?? '--'}</div>
                </div>
                <div>
                  <div className="label">请求次数</div>
                  <div className="value">{record.debug?.attempts ?? '--'}</div>
                </div>
              </div>
              <div className="section-title" style={{ marginTop: 18 }}>
                Token 用量
              </div>
              <pre className="raw-prompt">{JSON.stringify(record.usage_total ?? {}, null, 2)}</pre>
            </div>
            <div className="panel">
              <div className="section-title">调试信息</div>
              <pre className="raw-prompt">{JSON.stringify(record.debug ?? {}, null, 2)}</pre>
              <div className="section-title" style={{ marginTop: 18 }}>
                异常
              </div>
              <pre className="raw-prompt">{JSON.stringify(record.exception ?? {}, null, 2)}</pre>
            </div>
          </div>
        ) : null}
      </div>

      {record ? (
        <div className="panel">
          <div className="section-title">
            <Bot size={15} />
            分析后追问
          </div>
          <div className="field">
            <textarea
              className="followup-input"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="例如：当前方案最容易被什么走势证伪？"
            />
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button
              className="button"
              onClick={() => followup.mutate()}
              disabled={followup.isPending || !question.trim()}
            >
              <Sparkles size={14} />
              {followup.isPending ? '思考中...' : '发送追问'}
            </button>
            {followup.data ? <span className={confidenceBadge(confidence)}>已回答</span> : null}
          </div>
          {followup.data ? <pre className="raw-prompt">{followup.data.answer}</pre> : null}
        </div>
      ) : null}
    </div>
  );
}

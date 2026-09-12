import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  Bell,
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Download,
  Eye,
  Gauge,
  History,
  ListTree,
  MonitorUp,
  PlayCircle,
  Plus,
  RefreshCcw,
  Save,
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
  AIRecordSummary,
  BatchAnalyzeResponse,
  DatasetSummary,
  MonitorSchedule,
  MonitorTarget,
  MonitorTargetStatus,
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

const DEFAULT_MONITOR_SCHEDULE: MonitorSchedule = {
  mode: 'always',
  timezone: 'Asia/Shanghai',
  enabled: true,
  weekdays: [1, 2, 3, 4, 5],
  custom_start: '09:30',
  custom_end: '15:00',
};

const NEW_CUSTOM_TARGET: MonitorTarget = {
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

const WEEKDAY_OPTIONS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 7, label: '周日' },
];

function targetFromDataset(dataset: DatasetSummary): MonitorTarget {
  return {
    dataset_id: dataset.id,
    symbol: dataset.symbol,
    timeframe: dataset.timeframe,
    source: dataset.source ?? dataset.source_provider ?? '',
    enabled: true,
    analysis: {
      analysis_bar_count: 120,
      decision_stance: 'balanced',
      enable_next_bar_prediction: true,
    },
  };
}

function monitorTargetKey(target: MonitorTarget) {
  if (target.dataset_id) return `dataset:${target.dataset_id}`;
  return `${target.source}:${target.symbol}:${target.timeframe}:${target.exchange ?? ''}`;
}

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
}

function scheduleLabel(schedule?: MonitorSchedule) {
  if (!schedule) return '--';
  if (!schedule.enabled || schedule.mode === 'always') return '24小时';
  if (schedule.mode === 'a_share') return 'A股交易时段';
  return `${schedule.custom_start}-${schedule.custom_end}`;
}

function monitorErrorText(error?: Record<string, any> | null) {
  if (!error) return '';
  return String(error.message ?? error.detail ?? JSON.stringify(error));
}

function statusCount(
  status: MonitorTargetStatus | undefined,
  key: 'success_count' | 'failure_count' | 'skip_count',
) {
  const explicit = status?.[key];
  if (typeof explicit === 'number') return explicit;
  const lastStatus = status?.last_status ?? status?.status;
  if (key === 'success_count') {
    return lastStatus === 'ok' ? Math.max(1, status?.run_count ?? 0) : 0;
  }
  if (key === 'failure_count') return lastStatus === 'error' ? 1 : 0;
  return lastStatus === 'idle' ? 1 : 0;
}

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
  const [watchlistDatasetId, setWatchlistDatasetId] = useState('');
  const [expandedMonitor, setExpandedMonitor] = useState<string | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<MonitorSchedule>(
    DEFAULT_MONITOR_SCHEDULE,
  );
  const [scheduleReady, setScheduleReady] = useState(false);
  const [loadingRecordId, setLoadingRecordId] = useState('');
  const [lastBatch, setLastBatch] = useState<BatchAnalyzeResponse | null>(null);
  const watchlistSnapshot = useRef('');
  const watchlistHydrated = useRef(false);

  const configQuery = useQuery({ queryKey: ['system-config'], queryFn: api.getSystemConfig });
  const datasetQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const monitorQuery = useQuery({
    queryKey: ['monitor-status'],
    queryFn: api.getMonitorStatus,
    refetchInterval: 4000,
  });
  const historyQuery = useQuery({
    queryKey: ['ai-records', datasetId],
    queryFn: () => api.listAIRecords({ dataset_id: datasetId, limit: 50 }),
    enabled: Boolean(datasetId),
  });
  const datasets: DatasetSummary[] = datasetQuery.data?.items ?? [];
  const config = configQuery.data;

  const persistWatchlist = useMutation({
    mutationFn: (items: MonitorTarget[]) =>
      api.saveSystemConfig({ monitor_watchlist: items }),
    onSuccess: (value) => {
      watchlistSnapshot.current = JSON.stringify(value.monitor_watchlist);
      queryClient.setQueryData(['system-config'], value);
    },
    onError: (reason: Error) => setError(`盯盘列表保存失败：${reason.message}`),
  });

  useEffect(() => {
    if (!datasetId && datasets.length) setDatasetId(datasets[0].id);
  }, [datasetId, datasets]);

  useEffect(() => {
    if (!datasets.length) return;
    if (!watchlistDatasetId || !datasets.some((dataset) => dataset.id === watchlistDatasetId)) {
      setWatchlistDatasetId(datasets[0].id);
    }
  }, [datasets, watchlistDatasetId]);

  useEffect(() => {
    if (!config || watchlistHydrated.current) return;
    const seen = new Set<string>();
    const items = config.monitor_watchlist
      .map((target) => ({ ...target }))
      .filter((target) => {
        const key = monitorTargetKey(target);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    const sourceSchedule = config.monitor_schedule ?? DEFAULT_MONITOR_SCHEDULE;
    setWatchlist(items);
    watchlistSnapshot.current = JSON.stringify(items);
    setScheduleDraft({
      ...DEFAULT_MONITOR_SCHEDULE,
      ...sourceSchedule,
      weekdays: [...sourceSchedule.weekdays],
    });
    setScheduleReady(true);
    watchlistHydrated.current = true;
  }, [config]);

  useEffect(() => {
    if (!scheduleReady) return;
    const serialized = JSON.stringify(watchlist);
    if (serialized === watchlistSnapshot.current) return;
    const timer = window.setTimeout(() => persistWatchlist.mutate(watchlist), 400);
    return () => window.clearTimeout(timer);
  }, [persistWatchlist, scheduleReady, watchlist]);

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
      void queryClient.invalidateQueries({ queryKey: ['ai-records'] });
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

  const saveMonitorConfig = useMutation({
    mutationFn: async () => {
      const value = await api.saveSystemConfig({
        monitor_watchlist: watchlist,
        monitor_schedule: scheduleDraft,
      });
      if (monitorQuery.data?.running) {
        await api.stopMonitor();
        const targets = watchlist.filter((target) => target.enabled);
        if (targets.length) {
          await api.startMonitor({
            targets,
            interval_seconds: config?.analysis.monitor_interval_seconds ?? 60,
            auto_notify: config?.feishu.enabled ?? false,
            monitor_schedule: scheduleDraft,
          });
        }
      }
      return value;
    },
    onSuccess: (value) => {
      watchlistSnapshot.current = JSON.stringify(value.monitor_watchlist);
      queryClient.setQueryData(['system-config'], value);
      setNotice(
        monitorQuery.data?.running ? '盯盘配置已保存并重新启动' : '盯盘列表与调度配置已保存',
      );
      setError('');
      void queryClient.invalidateQueries({ queryKey: ['system-config'] });
      void queryClient.invalidateQueries({ queryKey: ['monitor-status'] });
    },
    onError: (reason: Error) => setError(`盯盘配置保存失败：${reason.message}`),
  });

  const startMonitor = useMutation({
    mutationFn: async () => {
      const value = await api.saveSystemConfig({
        monitor_watchlist: watchlist,
        monitor_schedule: scheduleDraft,
      });
      watchlistSnapshot.current = JSON.stringify(value.monitor_watchlist);
      queryClient.setQueryData(['system-config'], value);
      return api.startMonitor({
        targets: watchlist.filter((target) => target.enabled),
        interval_seconds: config?.analysis.monitor_interval_seconds ?? 60,
        auto_notify: config?.feishu.enabled ?? false,
        monitor_schedule: scheduleDraft,
      });
    },
    onSuccess: () => {
      setNotice('实时盯盘已启动');
      void queryClient.invalidateQueries({ queryKey: ['monitor-status'] });
      void queryClient.invalidateQueries({ queryKey: ['system-config'] });
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
          monitor_schedule: scheduleDraft,
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
      void queryClient.invalidateQueries({ queryKey: ['ai-records'] });
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

  const addDatasetToWatchlist = () => {
    const dataset = datasets.find((item) => item.id === watchlistDatasetId);
    if (!dataset) return;
    if (watchlist.some((target) => target.dataset_id === dataset.id)) {
      setNotice(`数据集已在盯盘列表中：${dataset.symbol} ${dataset.timeframe}`);
      return;
    }
    setWatchlist((current) => [...current, targetFromDataset(dataset)]);
    setNotice(`已加入盯盘：${dataset.title || dataset.symbol} ${dataset.timeframe}`);
  };

  const addCustomTarget = () => {
    setWatchlist((current) => [
      ...current,
      { ...NEW_CUSTOM_TARGET, analysis: { ...NEW_CUSTOM_TARGET.analysis } },
    ]);
    setNotice('已添加自定义盯盘标的');
  };

  const updateSchedule = (patch: Partial<MonitorSchedule>) => {
    setScheduleDraft((current) => ({ ...current, ...patch }));
  };

  const toggleScheduleWeekday = (weekday: number) => {
    setScheduleDraft((current) => {
      const selected = current.weekdays.includes(weekday);
      const weekdays = selected
        ? current.weekdays.filter((item) => item !== weekday)
        : [...current.weekdays, weekday].sort((left, right) => left - right);
      return { ...current, weekdays: weekdays.length ? weekdays : current.weekdays };
    });
  };

  const loadHistoryRecord = async (item: AIRecordSummary) => {
    setLoadingRecordId(item.id);
    setError('');
    try {
      const loaded = await api.getAIRecord(item.id);
      setRecord(loaded);
      setMode('single');
      setView('decision');
      setNotice(`已载入历史分析：${item.symbol} ${item.timeframe}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingRecordId('');
    }
  };

  const chooseMonitorRecord = (
    target: MonitorTarget,
    status?: MonitorTargetStatus,
  ) => {
    const key = monitorTargetKey(target);
    setExpandedMonitor((current) => (current === key ? null : key));
    if (status?.last_record) {
      setRecord(status.last_record);
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
        <div className="stack">
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
                    {dataset.title || dataset.symbol} · {dataset.symbol} · {dataset.timeframe} ·{' '}
                    {dataset.bar_count} 根
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
          <div className="panel">
            <div className="section-title">
              <div className="section-title-main">
                <History size={15} />
                历史分析结果
                {historyQuery.data?.items.length ? (
                  <span className="tag">{historyQuery.data.items.length} 条</span>
                ) : null}
              </div>
              <div className="section-title-actions">
                <span className="muted">{datasetId ? '按当前数据集筛选' : '选择数据集后显示记录'}</span>
                <button
                  className="button"
                  onClick={() => void historyQuery.refetch()}
                  disabled={!datasetId || historyQuery.isFetching}
                >
                  <RefreshCcw size={14} />
                  {historyQuery.isFetching ? '刷新中...' : '刷新'}
                </button>
              </div>
            </div>
            <div className="table-wrap">
              <table className="table history-table">
                <thead>
                  <tr>
                    <th>标的</th>
                    <th>周期</th>
                    <th>状态</th>
                    <th>决策</th>
                    <th>置信度</th>
                    <th>耗时</th>
                    <th>分析时间</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {historyQuery.isLoading ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="empty">正在加载历史分析记录...</div>
                      </td>
                    </tr>
                  ) : historyQuery.isError ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="empty">
                          历史记录加载失败：
                          {historyQuery.error instanceof Error
                            ? historyQuery.error.message
                            : '未知错误'}
                        </div>
                      </td>
                    </tr>
                  ) : (historyQuery.data?.items.length ?? 0) === 0 ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="empty">当前数据集还没有已保存的分析记录。</div>
                      </td>
                    </tr>
                  ) : (
                    historyQuery.data?.items.map((item) => {
                      const itemAction = item.action ?? item.decision_action ?? '--';
                      const itemConfidence =
                        typeof item.confidence === 'number' ? item.confidence : null;
                      return (
                        <tr
                          key={item.id}
                          className="history-row"
                          onClick={() => void loadHistoryRecord(item)}
                        >
                          <td>
                            <strong>{item.symbol}</strong>
                          </td>
                          <td>{item.timeframe}</td>
                          <td>
                            <span
                              className={
                                item.status === 'ok'
                                  ? 'badge badge-ok'
                                  : item.status === 'error'
                                    ? 'badge badge-danger'
                                    : 'badge badge-neutral'
                              }
                            >
                              {item.status}
                            </span>
                          </td>
                          <td>
                            {itemAction !== '--' ? (
                              <span className={decisionBadge(itemAction)}>{itemAction}</span>
                            ) : (
                              '--'
                            )}
                          </td>
                          <td>
                            {itemConfidence !== null
                              ? `${Math.round(itemConfidence)}%`
                              : '--'}
                          </td>
                          <td>
                            {typeof item.duration_ms === 'number'
                              ? `${Math.round(item.duration_ms)} ms`
                              : '--'}
                          </td>
                          <td>{formatDateTime(item.created_at)}</td>
                          <td>
                            <button
                              className="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                void loadHistoryRecord(item);
                              }}
                              disabled={loadingRecordId === item.id}
                            >
                              <Eye size={14} />
                              {loadingRecordId === item.id ? '载入中...' : '载入'}
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : (
        <div className="panel">
          <div className="section-title">
            <div className="section-title-main">
              <MonitorUp size={15} />
              实时盯盘
              <span
                className={
                  monitorQuery.data?.running ? 'badge badge-ok' : 'badge badge-neutral'
                }
              >
                {monitorQuery.data?.running ? '运行中' : '已停止'}
              </span>
              <span
                className={
                  monitorQuery.data?.schedule_active_now
                    ? 'badge badge-ok'
                    : 'badge badge-warn'
                }
              >
                {monitorQuery.data?.schedule_active_now ? '当前允许检查' : '当前不在允许时段'}
              </span>
              <span className="tag">
                {monitorQuery.data?.schedule_label ?? scheduleLabel(scheduleDraft)}
              </span>
            </div>
            <div className="section-title-actions">
              <button
                className="button"
                onClick={() => void monitorQuery.refetch()}
                disabled={monitorQuery.isFetching}
              >
                <RefreshCcw size={14} />
                {monitorQuery.isFetching ? '刷新中...' : '刷新状态'}
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
                  disabled={startMonitor.isPending || watchlist.length === 0}
                >
                  <PlayCircle size={14} />
                  {startMonitor.isPending ? '启动中...' : '启动盯盘'}
                </button>
              )}
            </div>
          </div>

          <div className="monitor-config-grid">
            <section className="monitor-config-block">
              <div className="section-title compact-title">
                <span>监控数据集</span>
                <span className="muted">{watchlist.length} 个</span>
              </div>
              <div className="monitor-add-row">
                <select
                  aria-label="选择要加入盯盘的数据集"
                  value={watchlistDatasetId}
                  onChange={(event) => setWatchlistDatasetId(event.target.value)}
                >
                  {datasets.length === 0 ? <option value="">暂无数据集</option> : null}
                  {datasets.map((dataset) => (
                    <option key={dataset.id} value={dataset.id}>
                      {dataset.title || dataset.symbol} · {dataset.symbol} · {dataset.timeframe}
                    </option>
                  ))}
                </select>
                <div className="row">
                  <button
                    className="button"
                    onClick={addDatasetToWatchlist}
                    disabled={!watchlistDatasetId}
                  >
                    <Plus size={14} />
                    加入数据集
                  </button>
                  <button className="button" onClick={addCustomTarget}>
                    <Plus size={14} />
                    自定义标的
                  </button>
                </div>
              </div>
              <div className="row">
                <button
                  className="button"
                  onClick={() => void batchAnalysis.mutate()}
                  disabled={batchAnalysis.isPending || watchlist.length === 0}
                >
                  <WandSparkles size={14} />
                  {batchAnalysis.isPending ? '批量分析中...' : '立即批量分析'}
                </button>
                <button
                  className="button"
                  onClick={() => runMonitorOnce.mutate()}
                  disabled={runMonitorOnce.isPending || watchlist.length === 0}
                >
                  <RefreshCcw size={14} />
                  {runMonitorOnce.isPending ? '检查中...' : '立即检查'}
                </button>
              </div>
              <div className="monitor-runtime-meta">
                <span>轮询 {monitorQuery.data?.interval_seconds ?? config?.analysis.monitor_interval_seconds ?? 60}s</span>
                <span>最近轮询 {formatDateTime(monitorQuery.data?.last_cycle_at)}</span>
              </div>
            </section>

            <section className="monitor-config-block">
              <div className="section-title compact-title">
                <span>调度时段</span>
                <span className="muted">
                  {scheduleDraft.enabled ? scheduleLabel(scheduleDraft) : '已停用约束'}
                </span>
              </div>
              <div className="monitor-schedule-fields">
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={scheduleDraft.enabled}
                    onChange={(event) => updateSchedule({ enabled: event.target.checked })}
                  />
                  启用时段约束
                </label>
                <div className="field">
                  <label htmlFor="monitor-schedule-mode">模式</label>
                  <select
                    id="monitor-schedule-mode"
                    value={scheduleDraft.mode}
                    disabled={!scheduleDraft.enabled}
                    onChange={(event) =>
                      updateSchedule({
                        mode: event.target.value as MonitorSchedule['mode'],
                      })
                    }
                  >
                    <option value="always">24小时</option>
                    <option value="a_share">A股交易时段</option>
                    <option value="custom">自定义</option>
                  </select>
                </div>
                {scheduleDraft.mode === 'custom' ? (
                  <>
                    <div className="field">
                      <label htmlFor="monitor-schedule-start">开始时间</label>
                      <input
                        id="monitor-schedule-start"
                        type="time"
                        value={scheduleDraft.custom_start}
                        disabled={!scheduleDraft.enabled}
                        onChange={(event) => updateSchedule({ custom_start: event.target.value })}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="monitor-schedule-end">结束时间</label>
                      <input
                        id="monitor-schedule-end"
                        type="time"
                        value={scheduleDraft.custom_end}
                        disabled={!scheduleDraft.enabled}
                        onChange={(event) => updateSchedule({ custom_end: event.target.value })}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="monitor-schedule-timezone">时区</label>
                      <input
                        id="monitor-schedule-timezone"
                        value={scheduleDraft.timezone}
                        disabled={!scheduleDraft.enabled}
                        onChange={(event) => updateSchedule({ timezone: event.target.value })}
                        placeholder="Asia/Shanghai"
                      />
                    </div>
                    <div className="field monitor-weekdays">
                      <span>执行日</span>
                      <div className="weekday-picker">
                        {WEEKDAY_OPTIONS.map((weekday) => (
                          <label key={weekday.value} className="weekday-option">
                            <input
                              type="checkbox"
                              checked={scheduleDraft.weekdays.includes(weekday.value)}
                              disabled={!scheduleDraft.enabled}
                              onChange={() => toggleScheduleWeekday(weekday.value)}
                            />
                            {weekday.label}
                          </label>
                        ))}
                      </div>
                    </div>
                  </>
                ) : null}
                {scheduleDraft.mode === 'a_share' && scheduleDraft.enabled ? (
                  <div className="monitor-schedule-note">
                    Asia/Shanghai · 09:30-11:30、13:00-15:00 · 周一至周五
                  </div>
                ) : null}
              </div>
              <div className="row">
                <button
                  className="button"
                  onClick={() => saveMonitorConfig.mutate()}
                  disabled={saveMonitorConfig.isPending || persistWatchlist.isPending}
                >
                  <Save size={14} />
                  {saveMonitorConfig.isPending ? '保存中...' : '保存盯盘配置'}
                </button>
                <span className="muted">
                  下次检查 {formatDateTime(monitorQuery.data?.next_check_at)}
                </span>
              </div>
            </section>
          </div>

          <div className="monitor-status-list">
            {watchlist.length === 0 ? (
              <div className="empty">从上方数据集选择器加入监控项后开始盯盘。</div>
            ) : (
              watchlist.map((target, index) => {
                const targetKey = monitorTargetKey(target);
                const status = monitorItems.find(
                  (item) => monitorTargetKey(item.target) === targetKey,
                );
                const dataset = datasets.find((item) => item.id === target.dataset_id);
                const itemRecord = status?.last_record;
                const itemAction =
                  status?.last_action ?? (itemRecord ? actionOf(itemRecord) : '--');
                const itemConfidence =
                  status?.last_confidence ??
                  (itemRecord ? confidenceOf(itemRecord) : null);
                const allowed =
                  status?.schedule_active_now ??
                  monitorQuery.data?.schedule_active_now ??
                  false;
                const expanded = expandedMonitor === targetKey;
                const errorText = monitorErrorText(status?.last_error);
                return (
                  <div
                    className={expanded ? 'monitor-status-item expanded' : 'monitor-status-item'}
                    key={targetKey}
                  >
                    <div
                      className="monitor-status-row"
                      tabIndex={0}
                      onClick={() => chooseMonitorRecord(target, status)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          chooseMonitorRecord(target, status);
                        }
                      }}
                    >
                      <div className="monitor-status-target">
                        <input
                          type="checkbox"
                          checked={target.enabled}
                          onClick={(event) => event.stopPropagation()}
                          onChange={(event) =>
                            updateTarget(index, { enabled: event.target.checked })
                          }
                          aria-label={`${dataset?.symbol ?? target.symbol} 启用状态`}
                        />
                        <div>
                          <strong>{dataset?.title || target.symbol}</strong>
                          <span>
                            {target.symbol} · {target.timeframe}
                            {dataset?.source ? ` · ${dataset.source}` : ''}
                          </span>
                        </div>
                      </div>
                      <div className="monitor-status-cell">
                        <span className="label">状态</span>
                        <span
                          className={
                            status?.status === 'error'
                              ? 'badge badge-danger'
                              : status?.status === 'ok'
                                ? 'badge badge-ok'
                                : 'badge badge-neutral'
                          }
                        >
                          {status?.status ?? '未运行'}
                        </span>
                      </div>
                      <div className="monitor-status-cell">
                        <span className="label">允许时段</span>
                        <span className={allowed ? 'badge badge-ok' : 'badge badge-warn'}>
                          {allowed ? '是' : '否'}
                        </span>
                      </div>
                      <div className="monitor-status-cell">
                        <span className="label">下次检查</span>
                        <strong>
                          {formatDateTime(
                            status?.next_check_at ?? monitorQuery.data?.next_check_at,
                          )}
                        </strong>
                      </div>
                      <div className="monitor-status-cell">
                        <span className="label">最近检查</span>
                        <strong>
                          {formatDateTime(status?.last_check_at ?? status?.last_run_at)}
                        </strong>
                      </div>
                      <div className="monitor-status-cell">
                        <span className="label">成功/失败/跳过</span>
                        <strong>
                          {statusCount(status, 'success_count')}/
                          {statusCount(status, 'failure_count')}/
                          {statusCount(status, 'skip_count')}
                        </strong>
                      </div>
                      <div className="monitor-status-cell">
                        <span className="label">决策</span>
                        <strong>
                          {itemAction}
                          {itemConfidence !== null ? ` · ${Math.round(itemConfidence)}%` : ''}
                        </strong>
                      </div>
                      {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </div>
                    {expanded ? (
                      <div className="monitor-status-detail">
                        <div className="monitor-detail-grid">
                          <div className="meta-item">
                            <div className="label">数据集</div>
                            <div className="value">
                              {dataset
                                ? `${dataset.title || dataset.symbol} · ${dataset.id}`
                                : '旧配置项（未绑定数据集）'}
                            </div>
                          </div>
                          <div className="meta-item">
                            <div className="label">最近行情</div>
                            <div className="value">{status?.last_session ?? '--'}</div>
                          </div>
                          <div className="meta-item">
                            <div className="label">运行次数 / 新增 K 线</div>
                            <div className="value">
                              {status?.run_count ?? 0} / {status?.new_bar_count ?? 0}
                            </div>
                          </div>
                          <div className="meta-item">
                            <div className="label">最近决策</div>
                            <div className="value">
                              {itemAction}
                              {itemConfidence !== null
                                ? ` · ${Math.round(itemConfidence)}%`
                                : ''}
                            </div>
                          </div>
                          <div className="meta-item monitor-detail-error">
                            <div className="label">最近错误</div>
                            <div className={errorText ? 'value error-text' : 'value'}>
                              {errorText || '无'}
                            </div>
                          </div>
                          <div className="meta-item">
                            <div className="label">分析 K 线</div>
                            <div className="value">
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
                            </div>
                          </div>
                          {!target.dataset_id ? (
                            <>
                              <div className="meta-item">
                                <div className="label">标的代码</div>
                                <input
                                  value={target.symbol}
                                  onClick={(event) => event.stopPropagation()}
                                  onChange={(event) =>
                                    updateTarget(index, { symbol: event.target.value })
                                  }
                                  placeholder="XAUUSD / 600519"
                                />
                              </div>
                              <div className="meta-item">
                                <div className="label">数据源</div>
                                <select
                                  value={target.source}
                                  onClick={(event) => event.stopPropagation()}
                                  onChange={(event) =>
                                    updateTarget(index, { source: event.target.value })
                                  }
                                >
                                  <option value="yfinance">YFinance</option>
                                  <option value="akshare">AkShare</option>
                                  <option value="tradingview">TradingView</option>
                                  <option value="mt5">MT5</option>
                                </select>
                              </div>
                              <div className="meta-item">
                                <div className="label">周期</div>
                                <select
                                  value={target.timeframe}
                                  onClick={(event) => event.stopPropagation()}
                                  onChange={(event) =>
                                    updateTarget(index, { timeframe: event.target.value })
                                  }
                                >
                                  {['1m', '5m', '15m', '30m', '1h', '1d', '1w'].map(
                                    (timeframe) => (
                                      <option key={timeframe} value={timeframe}>
                                        {timeframe}
                                      </option>
                                    ),
                                  )}
                                </select>
                              </div>
                            </>
                          ) : null}
                        </div>
                        <div className="row">
                          {itemRecord ? (
                            <button
                              className="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setRecord(itemRecord);
                                setView('decision');
                              }}
                            >
                              <Eye size={14} />
                              打开最近结果
                            </button>
                          ) : (
                            <span className="muted">该监控项还没有可打开的分析结果。</span>
                          )}
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
                            移除
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })
            )}
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
          {record ? <span className="tag">{record.symbol || record.dataset_id || '--'}</span> : null}
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

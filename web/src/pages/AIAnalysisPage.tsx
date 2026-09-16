import { useEffect, useMemo, useRef, useState } from 'react';
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Activity,
  Bell,
  Bot,
  CalendarClock,
  ChevronDown,
  ChevronLeft,
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
import {
  DEFAULT_ANALYSIS_TIMEFRAME,
  DEFAULT_REALTIME_TIMEFRAME,
  REALTIME_TIMEFRAMES,
  datasetForTimeframe,
  findStockGroup,
  formatTimeframeLabel,
  groupDatasetsByStock,
  preferredDataset,
} from '../utils/datasetDisplay';
import {
  beginAIAnalysisRequest,
  finishAIAnalysisRequest,
  hasActiveAIAnalysisRequest,
  isAIAnalysisPageUnloading,
  updateAIAnalysisSession,
  useAIAnalysisSession,
  type AIAnalysisMode,
  type AIAnalysisViewKey,
} from '../state/aiAnalysisSession';

const VIEWS = [
  { key: 'live', label: '实时分析', icon: Activity },
  { key: 'diagnosis', label: '诊断', icon: Eye },
  { key: 'decision', label: '决策', icon: Gauge },
  { key: 'visualization', label: '决策可视化', icon: ListTree },
  { key: 'future', label: '未来走势', icon: TrendingUp },
  { key: 'raw', label: '原始', icon: Bot },
  { key: 'debug', label: '调试', icon: ShieldAlert },
] as const;

type ViewKey = AIAnalysisViewKey;

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

const HISTORY_PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

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

function isRetryableMonitorSaveError(reason: unknown) {
  if (!(reason instanceof Error)) return false;
  return (
    /请求失败（(502|503|504)）/.test(reason.message) ||
    /Failed to fetch|NetworkError|Load failed/i.test(reason.message)
  );
}

interface MonitorProductEntry {
  index: number;
  target: MonitorTarget;
  dataset?: DatasetSummary;
}

interface MonitorProductGroup {
  key: string;
  symbol: string;
  title: string;
  entries: MonitorProductEntry[];
}

function normalizeProductSymbol(value: string) {
  return value.trim().toUpperCase();
}

function groupWatchlistByProduct(
  watchlist: MonitorTarget[],
  datasets: DatasetSummary[],
): MonitorProductGroup[] {
  const datasetById = new Map(datasets.map((dataset) => [dataset.id, dataset]));
  const groups: MonitorProductGroup[] = [];
  const groupBySymbol = new Map<string, MonitorProductGroup>();

  watchlist.forEach((target, index) => {
    const dataset = target.dataset_id ? datasetById.get(target.dataset_id) : undefined;
    const symbol = normalizeProductSymbol(target.symbol || dataset?.symbol || '');
    const key = symbol
      ? `symbol:${symbol}`
      : `target:${monitorTargetKey(target)}:${index}`;
    let group = groupBySymbol.get(key);
    if (!group) {
      group = {
        key,
        symbol: symbol || '未命名产品',
        title: dataset?.title || symbol || '未命名产品',
        entries: [],
      };
      groupBySymbol.set(key, group);
      groups.push(group);
    }
    if (group.title === '未命名产品' && dataset?.title) group.title = dataset.title;
    group.entries.push({ index, target, dataset });
  });

  return groups;
}

function productStatus(statuses: Array<MonitorTargetStatus | undefined>) {
  const available = statuses.filter(
    (status): status is MonitorTargetStatus => Boolean(status),
  );
  if (!available.length) return { label: '未运行', className: 'badge badge-neutral' };
  if (available.some((status) => status.status === 'error' || status.last_status === 'error')) {
    return { label: '异常', className: 'badge badge-danger' };
  }
  if (available.some((status) => status.status === 'running')) {
    return { label: '运行中', className: 'badge badge-info' };
  }
  if (available.every((status) => status.status === 'idle')) {
    return { label: '空闲', className: 'badge badge-neutral' };
  }
  if (available.some((status) => status.status === 'ok' || status.last_status === 'ok')) {
    return { label: '已分析', className: 'badge badge-ok' };
  }
  return { label: '待运行', className: 'badge badge-warn' };
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
  const session = useAIAnalysisSession();
  const {
    mode,
    view,
    datasetId,
    record,
    streamLog,
    running,
    notice,
    error,
    question,
    lastBatch,
  } = session;
  const [importSymbol, setImportSymbol] = useState('GC=F');
  const [importTimeframe, setImportTimeframe] = useState('1d');
  const [importSource, setImportSource] = useState<
    'yfinance' | 'akshare' | 'tradingview' | 'mt5'
  >('yfinance');
  const [importExchange, setImportExchange] = useState('');
  const [watchlist, setWatchlist] = useState<MonitorTarget[]>([]);
  const [watchlistSymbol, setWatchlistSymbol] = useState('');
  const [watchlistTimeframe, setWatchlistTimeframe] = useState(
    DEFAULT_REALTIME_TIMEFRAME,
  );
  const [historyTimeframe, setHistoryTimeframe] = useState('');
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(20);
  const [expandedMonitor, setExpandedMonitor] = useState<string | null>(null);
  const [scheduleDraft, setScheduleDraft] = useState<MonitorSchedule>(
    DEFAULT_MONITOR_SCHEDULE,
  );
  const [scheduleReady, setScheduleReady] = useState(false);
  const [loadingRecordId, setLoadingRecordId] = useState('');
  const watchlistSnapshot = useRef('');
  const watchlistHydrated = useRef(false);

  const setMode = (value: AIAnalysisMode) =>
    updateAIAnalysisSession({ mode: value });
  const setView = (value: ViewKey) => updateAIAnalysisSession({ view: value });
  const setDatasetId = (value: string) =>
    updateAIAnalysisSession({ datasetId: value });
  const setRecord = (value: AIAnalysisRecord | null) =>
    updateAIAnalysisSession({ record: value });
  const setStreamLog = (value: string | ((current: string) => string)) =>
    updateAIAnalysisSession((current) => ({
      streamLog: typeof value === 'function' ? value(current.streamLog) : value,
    }));
  const setRunning = (value: boolean) =>
    updateAIAnalysisSession({ running: value });
  const setNotice = (value: string) =>
    updateAIAnalysisSession({ notice: value });
  const setError = (value: string) => updateAIAnalysisSession({ error: value });
  const setQuestion = (value: string) =>
    updateAIAnalysisSession({ question: value });
  const setLastBatch = (value: BatchAnalyzeResponse | null) =>
    updateAIAnalysisSession({ lastBatch: value });

  const configQuery = useQuery({ queryKey: ['system-config'], queryFn: api.getSystemConfig });
  const datasetQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const monitorQuery = useQuery({
    queryKey: ['monitor-status'],
    queryFn: api.getMonitorStatus,
    refetchInterval: 4000,
  });
  const datasets: DatasetSummary[] = datasetQuery.data?.items ?? [];
  const stockGroups = useMemo(() => groupDatasetsByStock(datasets), [datasets]);
  const historyTimeframes = useMemo(
    () => Array.from(new Set(datasets.map((dataset) => dataset.timeframe))).sort(),
    [datasets],
  );
  const selectedDataset = datasets.find((dataset) => dataset.id === datasetId);
  const selectedSymbol = selectedDataset?.symbol ?? '';
  const selectedTimeframe = selectedDataset?.timeframe ?? '';
  const selectedGroup = findStockGroup(stockGroups, selectedSymbol);
  const watchlistGroup = findStockGroup(stockGroups, watchlistSymbol);
  const watchlistTimeframes = (watchlistGroup?.datasets ?? []).filter((dataset) =>
    REALTIME_TIMEFRAMES.includes(
      dataset.timeframe as (typeof REALTIME_TIMEFRAMES)[number],
    ),
  );
  const watchlistDataset = watchlistTimeframes.find(
    (dataset) => dataset.timeframe === watchlistTimeframe,
  );
  const historyOffset = (historyPage - 1) * historyPageSize;
  const historyQuery = useQuery({
    queryKey: ['ai-records', historyTimeframe, historyPage, historyPageSize],
    queryFn: () =>
      api.listAIRecords({
        timeframe: historyTimeframe || undefined,
        limit: historyPageSize,
        offset: historyOffset,
      }),
    placeholderData: keepPreviousData,
  });
  const historyTotal = historyQuery.data?.total ?? 0;
  const historyTotalPages = Math.max(1, Math.ceil(historyTotal / historyPageSize));
  const historyDisplayedPage = historyQuery.data
    ? Math.floor(historyQuery.data.offset / historyQuery.data.limit) + 1
    : historyPage;
  const historyPageStart =
    historyTotal > 0 ? (historyQuery.data?.offset ?? historyOffset) + 1 : 0;
  const historyPageEnd =
    historyTotal > 0
      ? (historyQuery.data?.offset ?? historyOffset) +
        (historyQuery.data?.items.length ?? 0)
      : 0;
  const config = configQuery.data;

  const {
    mutate: persistWatchlist,
    isPending: persistWatchlistPending,
  } = useMutation({
    mutationFn: (items: MonitorTarget[]) =>
      api.saveSystemConfig({ monitor_watchlist: items }),
    retry: (failureCount, reason) =>
      failureCount < 2 && isRetryableMonitorSaveError(reason),
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 4000),
    onSuccess: (value, submitted) => {
      watchlistSnapshot.current = JSON.stringify(submitted);
      queryClient.setQueryData(['system-config'], value);
      updateAIAnalysisSession((current) =>
        current.error.startsWith('盯盘列表保存失败：') ? { error: '' } : {},
      );
    },
    onError: (reason: Error) => setError(`盯盘列表保存失败：${reason.message}`),
  });

  useEffect(() => {
    if (!datasets.length) return;
    if (!datasetId || !datasets.some((dataset) => dataset.id === datasetId)) {
      const preferred =
        datasets.find((dataset) => dataset.timeframe === DEFAULT_ANALYSIS_TIMEFRAME) ||
        preferredDataset(stockGroups[0]) ||
        datasets[0];
      setDatasetId(preferred.id);
    }
  }, [datasetId, datasets, stockGroups]);

  useEffect(() => {
    if (!stockGroups.length) return;
    const group = findStockGroup(stockGroups, watchlistSymbol) ?? stockGroups[0];
    if (group.symbol !== watchlistSymbol) setWatchlistSymbol(group.symbol);
    const selected = group.datasets.find(
      (dataset) =>
        dataset.timeframe === watchlistTimeframe &&
        REALTIME_TIMEFRAMES.includes(
          dataset.timeframe as (typeof REALTIME_TIMEFRAMES)[number],
        ),
    );
    if (!selected) {
      const fallback =
        datasetForTimeframe(group, DEFAULT_REALTIME_TIMEFRAME) ??
        group.datasets.find((dataset) =>
          REALTIME_TIMEFRAMES.includes(
            dataset.timeframe as (typeof REALTIME_TIMEFRAMES)[number],
          ),
        );
      setWatchlistTimeframe(fallback?.timeframe ?? '');
    }
  }, [stockGroups, watchlistSymbol, watchlistTimeframe]);

  useEffect(() => {
    if (!historyQuery.data) return;
    const lastPage = Math.max(1, Math.ceil(historyQuery.data.total / historyPageSize));
    if (historyPage > lastPage) setHistoryPage(lastPage);
  }, [historyPage, historyPageSize, historyQuery.data]);

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
    updateAIAnalysisSession((current) =>
      current.error.startsWith('盯盘列表保存失败：') ? { error: '' } : {},
    );
  }, [config]);

  useEffect(() => {
    if (!scheduleReady || persistWatchlistPending) return;
    const serialized = JSON.stringify(watchlist);
    if (serialized === watchlistSnapshot.current) return;
    const timer = window.setTimeout(() => persistWatchlist(watchlist), 400);
    return () => window.clearTimeout(timer);
  }, [persistWatchlist, persistWatchlistPending, scheduleReady, watchlist]);

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

  const runAnalysis = async ({ resume = false }: { resume?: boolean } = {}) => {
    if (!datasetId) return;
    const controller = beginAIAnalysisRequest();
    if (!controller) return;
    setRunning(true);
    if (resume) {
      setStreamLog((current) => `${current}[系统] 页面刷新后正在恢复实时分析...\n`);
      setNotice('正在恢复实时分析');
    } else {
      setRecord(null);
      setStreamLog('正在连接分析服务...\n');
      setNotice('');
    }
    setError('');
    if (!resume) {
      setMode('single');
      setView('live');
    }
    try {
      const result = await streamAIAnalysis(
        { dataset_id: datasetId },
        (event) => {
          if (event.type === 'snapshot') {
            setStreamLog(
              (current) =>
                `${current}快照 ${event.symbol} ${event.timeframe} ${event.bar_count} 根\n`,
            );
          } else if (
            ['log', 'stage1', 'stage1_reasoning', 'stage2', 'stage2_reasoning'].includes(
              event.type,
            )
          ) {
            const labels: Record<string, string> = {
              log: '系统',
              stage1: '阶段一正文',
              stage1_reasoning: '阶段一推理',
              stage2: '阶段二正文',
              stage2_reasoning: '阶段二推理',
            };
            setStreamLog(
              (current) =>
                `${current}[${labels[event.type] ?? event.type}] ${event.text ?? ''}`,
            );
          } else if (event.type === 'error') {
            const message = event.message ?? '分析失败';
            setError(message);
            setStreamLog((current) => `${current}[错误] ${message}\n`);
          } else if (event.type === 'done') {
            setStreamLog((current) => `${current}[系统] 分析流程已结束。\n`);
          }
        },
        controller.signal,
      );
      setRecord(result);
      setNotice(result.status === 'ok' ? '分析完成' : '分析返回异常记录');
      void queryClient.invalidateQueries({ queryKey: ['ai-records'] });
      if (result.status !== 'ok') setView('debug');
    } catch (reason) {
      if (
        isAIAnalysisPageUnloading() ||
        (reason instanceof DOMException && reason.name === 'AbortError')
      ) {
        return;
      }
      setNotice('');
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      finishAIAnalysisRequest(controller);
      if (!isAIAnalysisPageUnloading()) setRunning(false);
    }
  };

  useEffect(() => {
    if (!running || !datasetId || hasActiveAIAnalysisRequest()) return;
    void runAnalysis({ resume: true });
  }, [datasetId, running]);

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
      const submittedWatchlist = watchlist;
      const value = await api.saveSystemConfig({
        monitor_watchlist: submittedWatchlist,
        monitor_schedule: scheduleDraft,
      });
      if (monitorQuery.data?.running) {
        await api.stopMonitor();
        const targets = submittedWatchlist.filter((target) => target.enabled);
        if (targets.length) {
          await api.startMonitor({
            targets,
            interval_seconds: config?.analysis.monitor_interval_seconds ?? 60,
            auto_notify: config?.feishu.enabled ?? false,
            monitor_schedule: scheduleDraft,
          });
        }
      }
      return { value, submittedWatchlist };
    },
    onSuccess: ({ value, submittedWatchlist }) => {
      watchlistSnapshot.current = JSON.stringify(submittedWatchlist);
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
      const submittedWatchlist = watchlist;
      const value = await api.saveSystemConfig({
        monitor_watchlist: submittedWatchlist,
        monitor_schedule: scheduleDraft,
      });
      watchlistSnapshot.current = JSON.stringify(submittedWatchlist);
      queryClient.setQueryData(['system-config'], value);
      return api.startMonitor({
        targets: submittedWatchlist.filter((target) => target.enabled),
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
  const monitorProducts = useMemo(
    () => groupWatchlistByProduct(watchlist, datasets),
    [datasets, watchlist],
  );
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

  const updateProductEnabled = (entries: MonitorProductEntry[], enabled: boolean) => {
    const indexes = new Set(entries.map((entry) => entry.index));
    setWatchlist((current) =>
      current.map((target, index) =>
        indexes.has(index) ? { ...target, enabled } : target,
      ),
    );
  };

  const addWatchlistPeriod = () => {
    if (!watchlistDataset) return;
    if (
      watchlist.some(
        (target) =>
          target.symbol.trim().toUpperCase() ===
            watchlistDataset.symbol.trim().toUpperCase() &&
          target.timeframe.trim().toLowerCase() ===
            watchlistDataset.timeframe.trim().toLowerCase(),
      )
    ) {
      setNotice(
        `盯盘中已包含该周期：${watchlistDataset.symbol} ${watchlistDataset.timeframe}`,
      );
      return;
    }
    setWatchlist((current) => [...current, targetFromDataset(watchlistDataset)]);
    setNotice(
      `已加入盯盘：${watchlistDataset.title || watchlistDataset.symbol} ${watchlistDataset.timeframe}`,
    );
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
      if (item.dataset_id) setDatasetId(item.dataset_id);
      setMode('single');
      setView('decision');
      setNotice(`已载入分析结果：${item.symbol} ${item.timeframe}`);
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

  const selectAnalysisStock = (symbol: string) => {
    const group = findStockGroup(stockGroups, symbol);
    const dataset =
      datasetForTimeframe(group, selectedTimeframe) || preferredDataset(group);
    if (dataset) setDatasetId(dataset.id);
  };

  const selectAnalysisTimeframe = (timeframe: string) => {
    const dataset = datasetForTimeframe(selectedGroup, timeframe);
    if (dataset) setDatasetId(dataset.id);
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
          <span className="tag">
            {stockGroups.length} 只股票 / {datasets.length} 个周期
          </span>
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
            <small>选择股票与周期执行完整两阶段分析</small>
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
              <span className="muted">导入 500 根公开行情并更新该股票周期数据。</span>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">
              <WandSparkles size={15} />
              分析执行
              <span className="tag">两阶段</span>
            </div>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ai-stock">股票</label>
                <select
                  id="ai-stock"
                  value={selectedGroup?.symbol ?? ''}
                  onChange={(event) => selectAnalysisStock(event.target.value)}
                >
                  {stockGroups.length === 0 ? <option value="">暂无股票</option> : null}
                  {stockGroups.map((group) => (
                    <option key={group.symbol} value={group.symbol}>
                      {group.title || group.symbol} · {group.symbol}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="ai-analysis-timeframe">分析周期</label>
                <select
                  id="ai-analysis-timeframe"
                  value={selectedTimeframe}
                  onChange={(event) => selectAnalysisTimeframe(event.target.value)}
                >
                  {(selectedGroup?.datasets ?? []).map((dataset) => (
                    <option key={dataset.id} value={dataset.timeframe}>
                      {formatTimeframeLabel(dataset.timeframe)} · {dataset.bar_count} 根
                    </option>
                  ))}
                </select>
              </div>
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
                分析结果
                {historyQuery.data ? <span className="tag">{historyTotal} 条</span> : null}
              </div>
              <div className="section-title-actions">
                <select
                  value={historyTimeframe}
                  onChange={(event) => {
                    setHistoryTimeframe(event.target.value);
                    setHistoryPage(1);
                  }}
                  disabled={!historyTimeframes.length}
                  aria-label="按周期筛选分析结果"
                >
                  <option value="">全部周期</option>
                  {historyTimeframes.map((timeframe) => (
                    <option key={timeframe} value={timeframe}>
                      {formatTimeframeLabel(timeframe)}
                    </option>
                  ))}
                </select>
                <button
                  className="button"
                  onClick={() => void historyQuery.refetch()}
                  disabled={historyQuery.isFetching}
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
                        <div className="empty">正在加载分析结果...</div>
                      </td>
                    </tr>
                  ) : historyQuery.isError ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="empty">
                          分析结果加载失败：
                          {historyQuery.error instanceof Error
                            ? historyQuery.error.message
                            : '未知错误'}
                        </div>
                      </td>
                    </tr>
                  ) : (historyQuery.data?.items.length ?? 0) === 0 ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="empty">还没有已保存的分析结果。</div>
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
            {historyTotal > 0 ? (
              <div className="summary-pagination">
                <span className="muted">
                  第 {historyPageStart}-{historyPageEnd} 条，共 {historyTotal} 条
                </span>
                <div className="summary-page-actions">
                  <label className="history-page-size">
                    每页
                    <select
                      value={historyPageSize}
                      onChange={(event) => {
                        setHistoryPageSize(Number(event.target.value));
                        setHistoryPage(1);
                      }}
                      disabled={historyQuery.isFetching}
                    >
                      {HISTORY_PAGE_SIZE_OPTIONS.map((size) => (
                        <option key={size} value={size}>
                          {size}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="button"
                    type="button"
                    onClick={() => setHistoryPage((current) => Math.max(current - 1, 1))}
                    disabled={historyQuery.isFetching || historyPage <= 1}
                  >
                    <ChevronLeft size={14} />
                    上一页
                  </button>
                  <span className="summary-page-indicator">
                    第 {Math.min(historyDisplayedPage, historyTotalPages)} /{' '}
                    {historyTotalPages} 页
                  </span>
                  <button
                    className="button"
                    type="button"
                    onClick={() =>
                      setHistoryPage((current) =>
                        Math.min(current + 1, historyTotalPages),
                      )
                    }
                    disabled={historyQuery.isFetching || historyPage >= historyTotalPages}
                  >
                    下一页
                    <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            ) : null}
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
                <span>监控股票</span>
                <span className="muted">
                  {monitorProducts.length} 只股票 / {watchlist.length} 个周期
                </span>
              </div>
              <div className="monitor-add-row">
                <select
                  aria-label="选择要加入盯盘的股票"
                  value={watchlistGroup?.symbol ?? ''}
                  onChange={(event) => setWatchlistSymbol(event.target.value)}
                >
                  {stockGroups.length === 0 ? <option value="">暂无股票</option> : null}
                  {stockGroups.map((group) => (
                    <option key={group.symbol} value={group.symbol}>
                      {group.title || group.symbol} · {group.symbol}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="选择要加入盯盘的周期"
                  value={watchlistTimeframe}
                  onChange={(event) => setWatchlistTimeframe(event.target.value)}
                  disabled={!watchlistTimeframes.length}
                >
                  {!watchlistTimeframes.length ? (
                    <option value="">暂无实时周期</option>
                  ) : null}
                  {watchlistTimeframes.map((dataset) => (
                    <option key={dataset.id} value={dataset.timeframe}>
                      {formatTimeframeLabel(dataset.timeframe)} · {dataset.bar_count} 根
                    </option>
                  ))}
                </select>
                <div className="row">
                  <button
                    className="button"
                    onClick={addWatchlistPeriod}
                    disabled={!watchlistDataset}
                  >
                    <Plus size={14} />
                    加入周期
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
                  disabled={saveMonitorConfig.isPending || persistWatchlistPending}
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
              <div className="empty">从上方选择股票与实时周期后开始盯盘。</div>
            ) : (
              monitorProducts.map((product) => {
                const statuses = product.entries.map((entry) =>
                  monitorItems.find(
                    (item) => monitorTargetKey(item.target) === monitorTargetKey(entry.target),
                  ),
                );
                const enabledCount = product.entries.filter(
                  (entry) => entry.target.enabled,
                ).length;
                const allEnabled = enabledCount === product.entries.length;
                const someEnabled = enabledCount > 0 && !allEnabled;
                const aggregateStatus = productStatus(statuses);

                return (
                  <div className="monitor-status-item monitor-product-group" key={product.key}>
                    <div className="monitor-product-header">
                      <div className="monitor-product-summary">
                        <input
                          type="checkbox"
                          checked={allEnabled}
                          ref={(node) => {
                            if (node) node.indeterminate = someEnabled;
                          }}
                          onChange={(event) =>
                            updateProductEnabled(product.entries, event.target.checked)
                          }
                          aria-label={`${product.title} 全部周期启用状态`}
                        />
                        <div>
                          <strong>{product.title}</strong>
                          <span>
                            {product.symbol} · {product.entries.length} 个周期
                          </span>
                        </div>
                      </div>
                      <div className="monitor-product-meta">
                        <span className="tag">
                          已启用 {enabledCount}/{product.entries.length}
                        </span>
                        <span className={aggregateStatus.className}>{aggregateStatus.label}</span>
                      </div>
                    </div>
                    <div className="monitor-product-periods">
                      {product.entries.map(({ target, index, dataset }) => {
                        const targetKey = monitorTargetKey(target);
                        const status = monitorItems.find(
                          (item) => monitorTargetKey(item.target) === targetKey,
                        );
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
                          <div key={targetKey}>
                            <div
                              className="monitor-status-row monitor-period-row"
                              tabIndex={0}
                              onClick={() => chooseMonitorRecord(target, status)}
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' || event.key === ' ') {
                                  event.preventDefault();
                                  chooseMonitorRecord(target, status);
                                }
                              }}
                            >
                              <div className="monitor-status-target monitor-period-target">
                                <input
                                  type="checkbox"
                                  checked={target.enabled}
                                  onClick={(event) => event.stopPropagation()}
                                  onChange={(event) =>
                                    updateTarget(index, { enabled: event.target.checked })
                                  }
                                  aria-label={`${dataset?.symbol ?? target.symbol} ${target.timeframe} 启用状态`}
                                />
                                <div>
                                  <strong>{formatTimeframeLabel(target.timeframe)}</strong>
                                  <span>
                                    {target.timeframe}
                                    {dataset?.source || target.source
                                      ? ` · ${dataset?.source ?? target.source}`
                                      : ''}
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
                                    status?.next_check_at ??
                                      monitorQuery.data?.next_check_at,
                                  )}
                                </strong>
                              </div>
                              <div className="monitor-status-cell">
                                <span className="label">最近检查</span>
                                <strong>
                                  {formatDateTime(
                                    status?.last_check_at ?? status?.last_run_at,
                                  )}
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
                                  {itemConfidence !== null
                                    ? ` · ${Math.round(itemConfidence)}%`
                                    : ''}
                                </strong>
                              </div>
                              {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                            </div>
                            {expanded ? (
                              <div className="monitor-status-detail">
                                <div className="monitor-detail-grid">
                                  <div className="meta-item">
                                    <div className="label">股票与周期</div>
                                    <div className="value">
                                      {dataset
                                        ? `${dataset.title || dataset.symbol} · ${formatTimeframeLabel(dataset.timeframe)}`
                                        : `${target.symbol} · ${formatTimeframeLabel(target.timeframe)}`}
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
                                              analysis_bar_count:
                                                Number(event.target.value) || 120,
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
                                            updateTarget(index, {
                                              symbol: event.target.value,
                                            })
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
                                            updateTarget(index, {
                                              source: event.target.value,
                                            })
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
                                            updateTarget(index, {
                                              timeframe: event.target.value,
                                            })
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
                                    <span className="muted">
                                      该周期还没有可打开的分析结果。
                                    </span>
                                  )}
                                  <button
                                    className="button button-danger"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setWatchlist((current) =>
                                        current.filter(
                                          (_, targetIndex) => targetIndex !== index,
                                        ),
                                      );
                                    }}
                                  >
                                    <Trash2 size={14} />
                                    移除周期
                                  </button>
                                </div>
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
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
          <div className="empty">选择股票与周期执行分析，或从盯盘列表打开最近结果。</div>
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

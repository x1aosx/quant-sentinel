import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Database, Globe, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../api/client';
import { TradingViewExchangeSelect } from '../components/TradingViewExchangeSelect';
import type { DatasetSummary, SyncDatasetResponse } from '../types';
import { formatSessionTime, formatTimeframeLabel } from '../utils/datasetDisplay';

type DatasetSource = 'yfinance' | 'akshare' | 'tradingview' | 'mt5';
type DatasetAdjust = 'qfq' | 'hfq' | 'none';

interface SyncPayload {
  source: DatasetSource;
  symbol: string;
  timeframe: string;
  lookback: number;
  adjust: DatasetAdjust;
  exchange?: string;
}

interface InstrumentGroup {
  key: string;
  symbol: string;
  title: string;
  datasets: DatasetSummary[];
}

const AUTO_UPDATE_INTERVALS = [
  { value: 60_000, label: '1 分钟' },
  { value: 300_000, label: '5 分钟' },
  { value: 900_000, label: '15 分钟' },
  { value: 1_800_000, label: '30 分钟' },
  { value: 3_600_000, label: '1 小时' },
  { value: 14_400_000, label: '4 小时' },
  { value: 86_400_000, label: '1 天' },
];

function sourceLabel(source?: DatasetSummary['source']): string {
  if (!source || source === 'local') return '本地文件';
  if (source === 'akshare') return 'AkShare';
  if (source === 'tradingview') return 'TradingView';
  if (source === 'mt5') return 'MT5';
  if (source === 'yfinance') return 'YFinance';
  return '未知';
}

function syncStatusLabel(status: SyncDatasetResponse['sync_status']): string {
  return status === 'updated' ? '已更新' : '无变化';
}

function formatTimestamp(value?: string): string {
  if (!value) return '未记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function formatRefreshTime(timestamp: number): string {
  if (!timestamp) return '尚未更新';
  const value = new Date(timestamp).toISOString();
  return formatTimestamp(value);
}

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

function remoteSourceForDataset(source?: DatasetSummary['source']): DatasetSource | null {
  if (source === 'akshare' || source === 'tradingview' || source === 'mt5') {
    return source;
  }
  if (source === 'yfinance') return source;
  return null;
}

function parseLookbackValue(value: string): number | null {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 10 || parsed > 5000) return null;
  return parsed;
}

export function DataCenterPage() {
  const queryClient = useQueryClient();
  const [source, setSource] = useState<DatasetSource>('yfinance');
  const [symbol, setSymbol] = useState('GC=F');
  const [timeframe, setTimeframe] = useState('1d');
  const [lookback, setLookback] = useState('500');
  const [adjust, setAdjust] = useState<DatasetAdjust>('qfq');
  const [exchange, setExchange] = useState('');
  const [syncingKey, setSyncingKey] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<SyncDatasetResponse | null>(null);
  const [notice, setNotice] = useState('');
  const [pageError, setPageError] = useState('');
  const [autoUpdate, setAutoUpdate] = useState(false);
  const [autoUpdateSymbol, setAutoUpdateSymbol] = useState('');
  const [updateInterval, setUpdateInterval] = useState(300_000);
  const syncLockRef = useRef(false);
  const autoRunRef = useRef(false);
  const datasetsRef = useRef<DatasetSummary[]>([]);
  const lookbackRef = useRef(lookback);
  const adjustRef = useRef(adjust);
  const autoUpdateRef = useRef(autoUpdate);
  const autoUpdateSymbolRef = useRef(autoUpdateSymbol);

  const datasetsQuery = useQuery({
    queryKey: ['datasets'],
    queryFn: api.listDatasets,
  });
  const datasets = datasetsQuery.data?.items ?? [];

  const instrumentGroups = useMemo(() => {
    const groups: InstrumentGroup[] = [];
    const groupBySymbol = new Map<string, InstrumentGroup>();

    datasets.forEach((dataset) => {
      const key = normalizeSymbol(dataset.symbol) || dataset.id;
      let group = groupBySymbol.get(key);
      if (!group) {
        group = {
          key,
          symbol: dataset.symbol,
          title: dataset.title || '',
          datasets: [],
        };
        groupBySymbol.set(key, group);
        groups.push(group);
      }
      if (!group.title && dataset.title) group.title = dataset.title;
      group.datasets.push(dataset);
    });

    return groups;
  }, [datasets]);

  const syncableGroups = useMemo(
    () =>
      instrumentGroups.filter((group) =>
        group.datasets.some((dataset) => remoteSourceForDataset(dataset.source)),
      ),
    [instrumentGroups],
  );

  const autoUpdateGroup = useMemo(
    () =>
      syncableGroups.find(
        (group) => normalizeSymbol(group.symbol) === normalizeSymbol(autoUpdateSymbol),
      ),
    [autoUpdateSymbol, syncableGroups],
  );

  useEffect(() => {
    datasetsRef.current = datasets;
  }, [datasets]);

  useEffect(() => {
    lookbackRef.current = lookback;
  }, [lookback]);

  useEffect(() => {
    adjustRef.current = adjust;
  }, [adjust]);

  useEffect(() => {
    autoUpdateRef.current = autoUpdate;
  }, [autoUpdate]);

  useEffect(() => {
    autoUpdateSymbolRef.current = autoUpdateSymbol;
  }, [autoUpdateSymbol]);

  useEffect(() => {
    const selectedStillExists = syncableGroups.some(
      (group) => normalizeSymbol(group.symbol) === normalizeSymbol(autoUpdateSymbol),
    );
    if (selectedStillExists) return;

    const fallbackSymbol = syncableGroups[0]?.symbol ?? '';
    setAutoUpdateSymbol(fallbackSymbol);
    if (!fallbackSymbol) setAutoUpdate(false);
  }, [autoUpdateSymbol, syncableGroups]);

  const executeSync = useCallback(
    async (
      key: string,
      payload: SyncPayload,
      options?: { automatic?: boolean },
    ): Promise<boolean> => {
      if (syncLockRef.current) return false;

      syncLockRef.current = true;
      setSyncingKey(key);
      try {
        const response = await api.syncRemoteDataset(payload);
        await queryClient.invalidateQueries({ queryKey: ['datasets'] });
        setLastSync(response);
        setNotice(
          `${options?.automatic ? '自动更新：' : ''}${response.title || response.symbol} ` +
            `${formatTimeframeLabel(response.timeframe)} 新增 ${response.inserted_count} 根，` +
            `已存在 ${response.updated_count} 根，总计 ${response.total_count} 根`,
        );
        setPageError('');
        return true;
      } catch (error) {
        setNotice('');
        setPageError(error instanceof Error ? error.message : '同步失败');
        return false;
      } finally {
        syncLockRef.current = false;
        setSyncingKey(null);
      }
    },
    [queryClient],
  );

  const runAutoUpdate = useCallback(async () => {
    if (!autoUpdateRef.current || !autoUpdateSymbolRef.current || autoRunRef.current) return;

    autoRunRef.current = true;
    try {
      const lookbackValue = parseLookbackValue(lookbackRef.current);
      if (lookbackValue === null) {
        setNotice('');
        setPageError('自动更新失败：回看数量必须是 10 到 5000 之间的整数');
        return;
      }

      const targetSymbol = normalizeSymbol(autoUpdateSymbolRef.current);
      const targetDatasets = datasetsRef.current
        .filter((dataset) => normalizeSymbol(dataset.symbol) === targetSymbol)
        .map((dataset) => ({
          dataset,
          source: remoteSourceForDataset(dataset.source),
        }))
        .filter(
          (
            target,
          ): target is { dataset: DatasetSummary; source: DatasetSource } =>
            target.source !== null,
        );
      if (targetDatasets.length === 0) {
        setNotice('');
        setPageError('自动更新标的没有可用的远程数据源，请重新选择');
        return;
      }

      for (const { dataset, source: datasetSource } of targetDatasets) {
        if (!autoUpdateRef.current) break;
        await executeSync(
          dataset.id,
          {
            source: datasetSource,
            symbol: dataset.symbol,
            timeframe: dataset.timeframe,
            lookback: lookbackValue,
            adjust: adjustRef.current,
            exchange: dataset.exchange,
          },
          { automatic: true },
        );
      }
    } finally {
      autoRunRef.current = false;
    }
  }, [executeSync]);

  useEffect(() => {
    if (!autoUpdate || !autoUpdateSymbol) return;

    void runAutoUpdate();
    const intervalId = window.setInterval(() => {
      void runAutoUpdate();
    }, updateInterval);
    return () => window.clearInterval(intervalId);
  }, [autoUpdate, autoUpdateSymbol, runAutoUpdate, updateInterval]);

  const deleteMutation = useMutation({
    mutationFn: (dataset: DatasetSummary) => api.deleteDataset(dataset.id),
    onSuccess: async (_, dataset) => {
      await queryClient.invalidateQueries({ queryKey: ['datasets'] });
      setLastSync((current) => (current?.id === dataset.id ? null : current));
      setNotice(
        `已删除 ${dataset.title || dataset.symbol} ${formatTimeframeLabel(dataset.timeframe)}`,
      );
      setPageError('');
    },
    onError: (error: Error) => {
      setNotice('');
      setPageError(`删除数据集失败：${error.message}`);
    },
  });

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPageError('');
    setNotice('');

    const symbolValue = symbol.trim().toUpperCase();
    const timeframeValue = timeframe.trim();
    const lookbackValue = parseLookbackValue(lookback);
    if (!symbolValue) {
      setPageError('请填写要同步的 symbol');
      return;
    }
    if (!timeframeValue) {
      setPageError('请选择要同步的 timeframe');
      return;
    }
    if (lookbackValue === null) {
      setPageError('回看数量必须是 10 到 5000 之间的整数');
      return;
    }

    void executeSync('form', {
      source,
      symbol: symbolValue,
      timeframe: timeframeValue,
      lookback: lookbackValue,
      adjust,
      exchange: source === 'tradingview' ? exchange : undefined,
    });
  };

  const handleSyncDataset = (dataset: DatasetSummary) => {
    setPageError('');
    setNotice('');
    const datasetSource = remoteSourceForDataset(dataset.source);
    if (!datasetSource) {
      setPageError('该数据集来自本地文件，不能远程同步');
      return;
    }
    const lookbackValue = parseLookbackValue(lookback);
    if (lookbackValue === null) {
      setPageError('请先把上方回看数量调整为 10 到 5000 之间的整数');
      return;
    }

    void executeSync(dataset.id, {
      source: datasetSource,
      symbol: dataset.symbol,
      timeframe: dataset.timeframe,
      lookback: lookbackValue,
      adjust,
      exchange: dataset.exchange,
    });
  };

  const handleDeleteDataset = (dataset: DatasetSummary) => {
    setPageError('');
    setNotice('');
    const target = `${dataset.title || dataset.symbol} ${formatTimeframeLabel(dataset.timeframe)}`;
    if (!window.confirm(`确定删除数据集“${target}”吗？此操作无法撤销。`)) return;
    deleteMutation.mutate(dataset);
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>数据中心</h1>
          <div className="muted">从公开行情接口实时拉取数据并增量同步，持续更新分析数据集。</div>
        </div>
        <span className="tag">
          {instrumentGroups.length} 个标的 / {datasets.length} 个数据集
        </span>
      </div>

      <div className="panel">
        <div className="section-title">
          <Globe size={15} />
          在线行情同步
          {lastSync ? <span className="tag">{syncStatusLabel(lastSync.sync_status)}</span> : null}
        </div>
        <form className="upload-panel" onSubmit={handleSubmit}>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="sync-source">数据源</label>
              <select
                id="sync-source"
                value={source}
                onChange={(event) => setSource(event.target.value as DatasetSource)}
              >
                <option value="yfinance">YFinance</option>
                <option value="akshare">AkShare / A股</option>
                <option value="tradingview">TradingView</option>
                <option value="mt5">MT5</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="sync-symbol">symbol</label>
              <input
                id="sync-symbol"
                value={symbol}
                onChange={(event) => setSymbol(event.target.value)}
                placeholder={
                  source === 'akshare'
                    ? '如 600519 或 SH600519'
                    : source === 'mt5'
                      ? '如 XAUUSDm 或 EURUSDm'
                      : '如 GC=F、AAPL、800865'
                }
              />
            </div>
            {source === 'tradingview' ? (
              <div className="field">
                <label htmlFor="sync-exchange">exchange</label>
                <TradingViewExchangeSelect
                  id="sync-exchange"
                  value={exchange}
                  onChange={setExchange}
                />
              </div>
            ) : null}
            <div className="field">
              <label htmlFor="sync-timeframe">周期</label>
              <select
                id="sync-timeframe"
                value={timeframe}
                onChange={(event) => setTimeframe(event.target.value)}
              >
                <option value="1d">1d</option>
                <option value="1w">1w</option>
                <option value="1h">1h</option>
                <option value="30m">30m</option>
                <option value="15m">15m</option>
                <option value="5m">5m</option>
                <option value="1m">1m</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="sync-lookback">回看K线</label>
              <input
                id="sync-lookback"
                type="number"
                min={10}
                max={5000}
                step={1}
                value={lookback}
                onChange={(event) => setLookback(event.target.value)}
              />
            </div>
            {source === 'yfinance' || source === 'akshare' ? (
              <div className="field">
                <label htmlFor="sync-adjust">复权</label>
                <select
                  id="sync-adjust"
                  value={adjust}
                  onChange={(event) => setAdjust(event.target.value as DatasetAdjust)}
                >
                  <option value="qfq">前复权</option>
                  <option value="hfq">后复权</option>
                  <option value="none">不复权</option>
                </select>
              </div>
            ) : null}
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button
              className="button button-primary"
              type="submit"
              disabled={syncingKey !== null}
            >
              <RefreshCw size={14} />
              {syncingKey === 'form' ? '同步中...' : '同步行情'}
            </button>
            <span className="muted">同步接口会拉取最新行情，并仅增量写入新增K线。</span>
          </div>
        </form>
      </div>

      {pageError ? (
        <div className="empty" role="alert">
          错误：{pageError}
        </div>
      ) : null}
      {notice ? (
        <div className="notice" role="status" aria-live="polite">
          {notice}
        </div>
      ) : null}

      {lastSync ? (
        <div className="panel">
          <div className="inline-meta">
            <div className="meta-item">
              <div className="label">新增</div>
              <div className="value">{lastSync.inserted_count} 根</div>
            </div>
            <div className="meta-item">
              <div className="label">已存在</div>
              <div className="value">{lastSync.updated_count} 根</div>
            </div>
            <div className="meta-item">
              <div className="label">总数</div>
              <div className="value">{lastSync.total_count} 根</div>
            </div>
            <div className="meta-item">
              <div className="label">同步状态</div>
              <div className="value">{syncStatusLabel(lastSync.sync_status)}</div>
            </div>
            <div className="meta-item">
              <div className="label">同步时间</div>
              <div className="value">{formatTimestamp(lastSync.synced_at)}</div>
            </div>
          </div>
        </div>
      ) : null}

      <div className="panel">
        <div className="section-title">
          <div className="section-title-main">
            <Database size={15} />
            数据集列表
            {lastSync ? (
              <span className="tag">
                最近同步 {lastSync.title || lastSync.symbol}{' '}
                {formatTimeframeLabel(lastSync.timeframe)}
              </span>
            ) : null}
          </div>
          <div className="section-title-actions">
            <span className="muted">自动更新标的</span>
            <select
              className="auto-update-target"
              aria-label="自动更新标的"
              value={autoUpdateSymbol}
              disabled={syncableGroups.length === 0}
              onChange={(event) => setAutoUpdateSymbol(event.target.value)}
            >
              {syncableGroups.map((group) => (
                <option key={group.key} value={group.symbol}>
                  {group.symbol}
                  {group.title ? ` / ${group.title}` : ''}
                </option>
              ))}
            </select>
            <label className="checkbox-row" htmlFor="dataset-auto-update">
              <input
                id="dataset-auto-update"
                type="checkbox"
                checked={autoUpdate}
                disabled={syncableGroups.length === 0}
                onChange={(event) => {
                  const checked = event.target.checked;
                  if (checked && !autoUpdateSymbol && syncableGroups[0]) {
                    setAutoUpdateSymbol(syncableGroups[0].symbol);
                  }
                  setAutoUpdate(checked);
                }}
              />
              启用
            </label>
            <select
              aria-label="自动更新频率"
              value={updateInterval}
              disabled={!autoUpdate}
              onChange={(event) => setUpdateInterval(Number(event.target.value))}
            >
              {AUTO_UPDATE_INTERVALS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {autoUpdate && autoUpdateGroup ? (
              <span className="muted">{autoUpdateGroup.datasets.length} 个周期</span>
            ) : null}
            <button
              className="button"
              type="button"
              onClick={() => void datasetsQuery.refetch()}
              disabled={datasetsQuery.isFetching}
            >
              <RefreshCw size={14} />
              {datasetsQuery.isFetching ? '刷新中...' : '刷新列表'}
            </button>
            <span className="muted">{formatRefreshTime(datasetsQuery.dataUpdatedAt)}</span>
          </div>
        </div>
        {datasetsQuery.isPending ? (
          <div className="empty">加载中...</div>
        ) : datasetsQuery.isError ? (
          <div className="empty">数据集列表加载失败：{(datasetsQuery.error as Error).message}</div>
        ) : datasets.length === 0 ? (
          <div className="empty">暂无数据集。请在上方同步在线行情。</div>
        ) : (
          <div className="table-wrap">
            <table className="table dataset-table">
              <thead>
                <tr>
                  <th scope="col">股票代号</th>
                  <th scope="col">中文名称</th>
                  <th scope="col">周期</th>
                  <th scope="col">数据源</th>
                  <th scope="col">K线数</th>
                  <th scope="col">起始时间</th>
                  <th scope="col">结束时间</th>
                  <th scope="col">最后同步</th>
                  <th scope="col">dataset_id</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {instrumentGroups.map((group) => (
                  <Fragment key={group.key}>
                    {group.datasets.map((dataset, index) => {
                      const isFirstInGroup = index === 0;
                      const isLastInGroup = index === group.datasets.length - 1;
                      const remoteSource = remoteSourceForDataset(dataset.source);
                      const isDeleting =
                        deleteMutation.isPending && deleteMutation.variables?.id === dataset.id;

                      return (
                        <tr key={dataset.id} className={isLastInGroup ? 'dataset-group-end' : ''}>
                          {isFirstInGroup ? (
                            <>
                              <td rowSpan={group.datasets.length} className="dataset-symbol-cell">
                                <div className="code">{group.symbol}</div>
                                <div className="muted dataset-group-count">
                                  {group.datasets.length} 个周期
                                </div>
                              </td>
                              <td rowSpan={group.datasets.length} className="dataset-title-cell">
                                {group.title || '--'}
                              </td>
                            </>
                          ) : null}
                          <td>
                            <div>{formatTimeframeLabel(dataset.timeframe)}</div>
                            <div className="muted code">{dataset.timeframe}</div>
                          </td>
                          <td>
                            <div>{sourceLabel(dataset.source)}</div>
                            {dataset.exchange ? (
                              <div className="muted">{dataset.exchange}</div>
                            ) : null}
                            {dataset.source_provider ? (
                              <div className="muted">{dataset.source_provider}</div>
                            ) : null}
                          </td>
                          <td>{dataset.bar_count}</td>
                          <td>{formatSessionTime(dataset.first_session)}</td>
                          <td>{formatSessionTime(dataset.last_session)}</td>
                          <td className="muted">
                            {formatTimestamp(dataset.synced_at ?? dataset.last_synced_at)}
                          </td>
                          <td className="code dataset-id" title={dataset.id}>
                            {dataset.id}
                          </td>
                          <td>
                            <div className="dataset-actions">
                              <button
                                className="button"
                                type="button"
                                onClick={() => handleSyncDataset(dataset)}
                                disabled={
                                  !remoteSource ||
                                  syncingKey !== null ||
                                  deleteMutation.isPending
                                }
                                title={
                                  remoteSource ? undefined : '本地数据集不能远程同步'
                                }
                              >
                                <RefreshCw size={14} />
                                {syncingKey === dataset.id ? '同步中...' : '同步最新'}
                              </button>
                              <button
                                className="button button-danger"
                                type="button"
                                onClick={() => handleDeleteDataset(dataset)}
                                disabled={syncingKey !== null || deleteMutation.isPending}
                              >
                                <Trash2 size={14} />
                                {isDeleting ? '删除中...' : '删除'}
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

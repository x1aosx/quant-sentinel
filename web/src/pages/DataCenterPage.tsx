import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Database, Globe, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import type { DatasetSummary, SyncDatasetResponse } from '../types';

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

function sourceLabel(source?: DatasetSummary['source']): string {
  if (source === 'akshare') return 'AkShare';
  if (source === 'tradingview') return 'TradingView';
  if (source === 'mt5') return 'MT5';
  if (source === 'local') return '本地文件';
  return 'YFinance';
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

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const datasets = datasetsQuery.data?.items ?? [];

  const syncMutation = useMutation({
    mutationFn: (payload: SyncPayload) => api.syncRemoteDataset(payload),
    onSuccess: async (response) => {
      await queryClient.invalidateQueries({ queryKey: ['datasets'] });
      setLastSync(response);
      setNotice(`新增 ${response.inserted_count} 根，已存在 ${response.updated_count} 根，总计 ${response.total_count} 根`);
      setPageError('');
    },
    onError: (error: Error) => {
      setNotice('');
      setPageError(error.message);
    },
    onSettled: () => setSyncingKey(null),
  });

  const parseLookback = (): number | null => {
    const value = Number(lookback);
    if (!Number.isInteger(value) || value < 10 || value > 5000) {
      setPageError('回看数量必须是 10 到 5000 之间的整数');
      return null;
    }
    return value;
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPageError('');
    setNotice('');

    const symbolValue = symbol.trim().toUpperCase();
    const timeframeValue = timeframe.trim();
    const lookbackValue = parseLookback();
    if (!symbolValue) {
      setPageError('请填写要同步的 symbol');
      return;
    }
    if (!timeframeValue) {
      setPageError('请选择要同步的 timeframe');
      return;
    }
    if (lookbackValue === null) return;

    setSyncingKey('form');
    syncMutation.mutate({
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
    const lookbackValue = parseLookback();
    if (lookbackValue === null) {
      setPageError('请先把上方回看数量调整为 10 到 5000 之间的整数');
      return;
    }

    setSyncingKey(dataset.id);
    syncMutation.mutate({
      source:
        dataset.source === 'akshare' ||
        dataset.source === 'tradingview' ||
        dataset.source === 'mt5'
          ? dataset.source
          : 'yfinance',
      symbol: dataset.symbol,
      timeframe: dataset.timeframe,
      lookback: lookbackValue,
      adjust,
      exchange: dataset.exchange,
    });
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>数据中心</h1>
          <div className="muted">从公开行情接口实时拉取数据并增量同步，持续更新分析数据集。</div>
        </div>
        <span className="tag">{datasets.length} 个数据集</span>
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
                <select
                  id="sync-exchange"
                  value={exchange}
                  onChange={(event) => setExchange(event.target.value)}
                >
                  <option value="">自动</option>
                  <option value="SSE">SSE</option>
                  <option value="SZSE">SZSE</option>
                  <option value="BSE">BSE</option>
                  <option value="HKEX">HKEX</option>
                  <option value="NASDAQ">NASDAQ</option>
                  <option value="NYSE">NYSE</option>
                  <option value="OANDA">OANDA</option>
                  <option value="TVC">TVC</option>
                  <option value="BINANCE">BINANCE</option>
                  <option value="CME_MINI">CME_MINI</option>
                </select>
              </div>
            ) : null}
            <div className="field">
              <label htmlFor="sync-timeframe">timeframe</label>
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
            <button className="button button-primary" type="submit" disabled={syncMutation.isPending}>
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
          <Database size={15} />
          数据集列表
          {lastSync ? <span className="tag">最近同步 {lastSync.symbol} {lastSync.timeframe}</span> : null}
        </div>
        {datasetsQuery.isPending ? (
          <div className="empty">加载中...</div>
        ) : datasetsQuery.isError ? (
          <div className="empty">数据集列表加载失败：{(datasetsQuery.error as Error).message}</div>
        ) : datasets.length === 0 ? (
          <div className="empty">暂无数据集。请在上方同步在线行情。</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">symbol</th>
                <th scope="col">timeframe</th>
                <th scope="col">数据源</th>
                <th scope="col">K线数</th>
                <th scope="col">起始 session</th>
                <th scope="col">结束 session</th>
                <th scope="col">最后同步</th>
                <th scope="col">dataset_id</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr key={dataset.id}>
                  <td className="code">{dataset.symbol}</td>
                  <td>{dataset.timeframe}</td>
                  <td>
                    <div>{sourceLabel(dataset.source)}</div>
                    {dataset.exchange ? <div className="muted">{dataset.exchange}</div> : null}
                    {dataset.source_provider ? <div className="muted">{dataset.source_provider}</div> : null}
                  </td>
                  <td>{dataset.bar_count}</td>
                  <td>{dataset.first_session}</td>
                  <td>{dataset.last_session}</td>
                  <td className="muted">
                    {formatTimestamp(dataset.synced_at ?? dataset.last_synced_at)}
                  </td>
                  <td className="code">{dataset.id}</td>
                  <td>
                    <button
                      className="button"
                      type="button"
                      onClick={() => handleSyncDataset(dataset)}
                      disabled={syncMutation.isPending}
                    >
                      <RefreshCw size={14} />
                      {syncingKey === dataset.id ? '同步中...' : '同步最新'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Database, Download, FileUp, Globe, Sparkles, Upload } from 'lucide-react';
import { api } from '../api/client';
import type { DatasetBar, DatasetSummary } from '../types';

const TIMEFRAME_ALIASES: Record<string, string> = {
  '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
  '60m': '1h', '1h': '1h', '2h': '2h', '4h': '4h',
  '1d': '1d', d: '1d', daily: '1d', day: '1d',
  '1w': '1w', w: '1w', weekly: '1w', week: '1w',
};

const FILENAME_NOISE = new Set(['data', 'bars', 'ohlc', 'kline', 'hist', 'history', 'candles']);

function inferFromFilename(name: string): { symbol?: string; timeframe?: string } {
  const base = name.replace(/\.[^.]+$/, '');
  const tokens = base.split(/[\s_\-().,]+/).filter(Boolean);
  let symbol: string | undefined;
  let timeframe: string | undefined;
  for (const token of tokens) {
    const key = token.toLowerCase();
    if (!timeframe && TIMEFRAME_ALIASES[key]) {
      timeframe = TIMEFRAME_ALIASES[key];
      continue;
    }
    if (!symbol && /^[a-z0-9]{1,16}$/i.test(token) && !FILENAME_NOISE.has(key)) {
      symbol = token.toUpperCase();
    }
  }
  return { symbol, timeframe };
}

function toFiniteNumber(raw: unknown, label: string, lineLabel: string): number {
  const text = String(raw ?? '').trim().replace(/^"|"$/g, '');
  if (typeof raw !== 'number' && text === '') {
    throw new Error(`${lineLabel}: ${label} 缺少数值`);
  }
  const value = typeof raw === 'number' ? raw : Number(text);
  if (!Number.isFinite(value)) {
    throw new Error(`${lineLabel}: ${label} 不是有效数字`);
  }
  return value;
}

function buildBar(raw: Record<string, unknown>, lineLabel: string): DatasetBar {
  const sessionId = String(raw.session_id ?? raw.session ?? raw.date ?? raw.time ?? '').trim();
  if (!sessionId) {
    throw new Error(`${lineLabel}: 缺少 session/date 字段`);
  }
  const open = toFiniteNumber(raw.open, 'open', lineLabel);
  const high = toFiniteNumber(raw.high, 'high', lineLabel);
  const low = toFiniteNumber(raw.low, 'low', lineLabel);
  const close = toFiniteNumber(raw.close, 'close', lineLabel);
  const volume = toFiniteNumber(raw.volume ?? raw.tick_volume, 'volume', lineLabel);
  if (open <= 0 || high <= 0 || low <= 0 || close <= 0) {
    throw new Error(`${lineLabel}: OHLC 必须大于 0`);
  }
  if (volume < 0) {
    throw new Error(`${lineLabel}: volume 不能为负数`);
  }
  if (low > Math.min(open, close) || high < Math.max(open, close)) {
    throw new Error(`${lineLabel}: high/low 未覆盖 open/close`);
  }
  return { session_id: sessionId, open, high, low, close, volume };
}

function collectErrors(errors: string[]): never {
  const shown = errors.slice(0, 5).join('；');
  const suffix = errors.length > 5 ? `（共 ${errors.length} 处问题）` : '';
  throw new Error(`${shown}${suffix}`);
}

function parseCsvBars(text: string): DatasetBar[] {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length < 2) {
    throw new Error('CSV 缺少表头或数据行');
  }
  const header = lines[0].split(',').map((cell) => cell.trim().toLowerCase().replace(/^"|"$/g, ''));
  const col = (...names: string[]) => header.findIndex((name) => names.includes(name));
  const sessionCol = col('session_id', 'session');
  const dateCol = col('date');
  const timeCol = col('time');
  const cols = {
    open: col('open'),
    high: col('high'),
    low: col('low'),
    close: col('close'),
    volume: col('volume', 'tick_volume'),
  };
  if (sessionCol < 0 && dateCol < 0 && timeCol < 0) {
    throw new Error('CSV 缺少 session/session_id/date/time 列');
  }
  const missing = Object.entries(cols).filter(([, index]) => index < 0).map(([name]) => name);
  if (missing.length) {
    throw new Error(`CSV 缺少列: ${missing.join(', ')}`);
  }

  const bars: DatasetBar[] = [];
  const errors: string[] = [];
  lines.slice(1).forEach((line, i) => {
    const lineLabel = `第 ${i + 2} 行`;
    try {
      const cells = line.split(',').map((cell) => cell.trim());
      const sessionId = sessionCol >= 0
        ? cells[sessionCol]
        : [cells[dateCol], cells[timeCol]].filter(Boolean).join(' ');
      bars.push(buildBar({ session_id: sessionId, open: cells[cols.open], high: cells[cols.high], low: cells[cols.low], close: cells[cols.close], volume: cells[cols.volume] }, lineLabel));
    } catch (err) {
      errors.push(err instanceof Error ? err.message : String(err));
    }
  });
  if (errors.length) {
    collectErrors(errors);
  }
  if (!bars.length) {
    throw new Error('CSV 中没有可用的数据行');
  }
  return bars;
}

function parseJsonBars(text: string): DatasetBar[] {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error('JSON 解析失败，请检查文件格式');
  }
  const rows = Array.isArray(data) ? data : (data as { bars?: unknown } | null)?.bars;
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('JSON 需要是 K 线数组，或包含 bars 数组的对象');
  }
  const bars: DatasetBar[] = [];
  const errors: string[] = [];
  rows.forEach((row, i) => {
    const lineLabel = `第 ${i + 1} 条`;
    try {
      if (typeof row !== 'object' || row === null) {
        throw new Error(`${lineLabel}: 不是对象`);
      }
      bars.push(buildBar(row as Record<string, unknown>, lineLabel));
    } catch (err) {
      errors.push(err instanceof Error ? err.message : String(err));
    }
  });
  if (errors.length) {
    collectErrors(errors);
  }
  return bars;
}

export function DataCenterPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileText, setFileText] = useState('');
  const [symbol, setSymbol] = useState('AAPL');
  const [timeframe, setTimeframe] = useState('1d');
  const [count, setCount] = useState('180');
  const [remoteSource, setRemoteSource] = useState<'yfinance' | 'akshare'>('yfinance');
  const [remoteSymbol, setRemoteSymbol] = useState('GC=F');
  const [remoteTimeframe, setRemoteTimeframe] = useState('1d');
  const [remoteLookback, setRemoteLookback] = useState('500');
  const [remoteAdjust, setRemoteAdjust] = useState<'qfq' | 'hfq' | 'none'>('qfq');
  const [selectedId, setSelectedId] = useState('');
  const [notice, setNotice] = useState('');
  const [pageError, setPageError] = useState('');

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const datasets = datasetsQuery.data?.items ?? [];

  const finishWith = (created: DatasetSummary, action: string) => {
    queryClient.invalidateQueries({ queryKey: ['datasets'] });
    setSelectedId(created.id);
    setNotice(`${action}成功：${created.symbol} ${created.timeframe}，${created.bar_count} 根K线，dataset_id=${created.id}`);
    setPageError('');
  };

  const uploadMutation = useMutation({
    mutationFn: (payload: { symbol: string; timeframe: string; bars: DatasetBar[] }) =>
      api.uploadDataset(payload),
    onSuccess: (created) => {
      setFile(null);
      setFileText('');
      if (fileInputRef.current) fileInputRef.current.value = '';
      finishWith(created, '导入');
    },
    onError: (err: Error) => {
      setNotice('');
      setPageError(err.message);
    },
  });

  const sampleMutation = useMutation({
    mutationFn: (timeframe?: string) =>
      api.generateSampleDataset(timeframe),
    onSuccess: (created) => finishWith(created, '生成演示数据'),
    onError: (err: Error) => {
      setNotice('');
      setPageError(err.message);
    },
  });

  const downloadMutation = useMutation({
    mutationFn: (payload: { symbol: string; timeframe: string; count: number }) =>
      api.downloadQuoteCsv(payload),
    onSuccess: (blob, payload) => {
      const safeName = (value: string) => value.replace(/[^a-zA-Z0-9._-]+/g, '_');
      const source = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = source;
      link.download = `${safeName(payload.symbol)}_${safeName(payload.timeframe)}_${payload.count}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(source);
      setNotice(`行情下载成功：${payload.symbol} ${payload.timeframe}，${payload.count} 根K线`);
      setPageError('');
    },
    onError: (err: Error) => {
      setNotice('');
      setPageError(err.message);
    },
  });

  const remoteMutation = useMutation({
    mutationFn: (payload: {
      source: 'yfinance' | 'akshare';
      symbol: string;
      timeframe: string;
      lookback: number;
      adjust: 'qfq' | 'hfq' | 'none';
    }) => api.importRemoteDataset(payload),
    onSuccess: (created) => finishWith(created, '网络导入'),
    onError: (err: Error) => {
      setNotice('');
      setPageError(err.message);
    },
  });

  const handleFileChange = (next: File | undefined) => {
    setPageError('');
    setNotice('');
    if (!next) {
      setFile(null);
      setFileText('');
      return;
    }
    const inferred = inferFromFilename(next.name);
    if (inferred.symbol) setSymbol(inferred.symbol);
    if (inferred.timeframe) setTimeframe(inferred.timeframe);
    const reader = new FileReader();
    reader.onload = () => {
      setFile(next);
      setFileText(String(reader.result ?? ''));
    };
    reader.onerror = () => {
      setFile(null);
      setFileText('');
      setPageError(`读取文件失败: ${next.name}`);
    };
    reader.readAsText(next);
  };

  const handleUpload = () => {
    if (!file || !fileText) {
      setPageError('请先选择要导入的 CSV/JSON 文件');
      return;
    }
    if (!symbol.trim() || !timeframe.trim()) {
      setPageError('请填写或从文件名推断 symbol 与 timeframe');
      return;
    }
    try {
      const isJson = file.name.toLowerCase().endsWith('.json') || /^[[{]/.test(fileText.trim());
      const bars = (isJson ? parseJsonBars(fileText) : parseCsvBars(fileText)).sort((a, b) => a.session_id.localeCompare(b.session_id));
      uploadMutation.mutate({ symbol: symbol.trim().toUpperCase(), timeframe: timeframe.trim(), bars });
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDownload = () => {
    setPageError('');
    setNotice('');
    const quoteCount = Number(count);
    if (!symbol.trim()) {
      setPageError('请填写要下载的 symbol');
      return;
    }
    if (!timeframe.trim()) {
      setPageError('请填写要下载的 timeframe');
      return;
    }
    if (!Number.isInteger(quoteCount) || quoteCount < 60 || quoteCount > 1000) {
      setPageError('count 需为 60-1000 之间的整数');
      return;
    }
    downloadMutation.mutate({
      symbol: symbol.trim().toUpperCase(),
      timeframe: timeframe.trim(),
      count: quoteCount,
    });
  };

  const handleRemoteImport = () => {
    setPageError('');
    setNotice('');
    const symbolValue = remoteSymbol.trim().toUpperCase();
    const timeframeValue = remoteTimeframe.trim();
    const lookback = Number(remoteLookback);
    if (!symbolValue) {
      setPageError('请填写要导入的 symbol');
      return;
    }
    if (!timeframeValue) {
      setPageError('请选择要导入的 timeframe');
      return;
    }
    if (!Number.isInteger(lookback) || lookback < 10 || lookback > 5000) {
      setPageError('回看数量必须是 10 到 5000 之间的整数');
      return;
    }
    remoteMutation.mutate({
      source: remoteSource,
      symbol: symbolValue,
      timeframe: timeframeValue,
      lookback,
      adjust: remoteAdjust,
    });
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>数据中心</h1>
          <div className="muted">导入本地或网络行情数据，供支撑阻力与价格行为分析使用。</div>
        </div>
        <span className="tag">{datasets.length} 个数据集</span>
      </div>

      <div className="grid grid-3">
        <div className="panel">
          <div className="section-title">
            <FileUp size={15} />
            导入本地数据
          </div>
          <div className="upload-panel">
            <input ref={fileInputRef} type="file" accept=".csv,.json" onChange={(e) => handleFileChange(e.target.files?.[0])} />
            <div className="form-grid">
              <div className="field">
                <label htmlFor="dataset-symbol">symbol</label>
                <input id="dataset-symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="如 AAPL" />
              </div>
              <div className="field">
                <label htmlFor="dataset-timeframe">timeframe</label>
                <input id="dataset-timeframe" value={timeframe} onChange={(e) => setTimeframe(e.target.value)} placeholder="如 1d" />
              </div>
              <div className="field">
                <label htmlFor="dataset-count">count</label>
                <input
                  id="dataset-count"
                  type="number"
                  min={60}
                  max={1000}
                  value={count}
                  onChange={(e) => setCount(e.target.value)}
                />
              </div>
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button className="button button-primary" onClick={handleUpload} disabled={uploadMutation.isPending}>
                <Upload size={14} />
                {uploadMutation.isPending ? '导入中...' : '解析并上传'}
              </button>
              <button className="button" onClick={handleDownload} disabled={downloadMutation.isPending}>
                <Download size={15} />
                {downloadMutation.isPending ? '下载中...' : '下载行情'}
              </button>
            </div>
            <span className="muted">支持 CSV（session/date、OHLC、volume/tick_volume）与 JSON（bar 数组或 {'{bars: []}'}）。</span>
            <span className="muted">下载行情使用确定性演示数据，不代表真实市场。</span>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <Sparkles size={15} />
            演示数据
          </div>
          <div className="upload-panel">
            <div className="row">
              <span className="inline-label">目标周期</span>
              <span className="tag">{timeframe.trim() || '默认周期'}</span>
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button
                className="button"
                onClick={() => sampleMutation.mutate(timeframe.trim() || undefined)}
                disabled={sampleMutation.isPending}
              >
                <Sparkles size={14} />
                {sampleMutation.isPending ? '生成中...' : '生成演示数据'}
              </button>
              <span className="muted">在上方 timeframe 输入框填写周期可指定生成结果。</span>
            </div>
            <p className="muted" style={{ margin: '12px 0 0' }}>演示数据仅用于研究流程验证，不代表真实市场。</p>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <Globe size={15} />
            网络数据导入
          </div>
          <div className="upload-panel">
            <div className="form-grid">
              <div className="field">
                <label htmlFor="remote-source">数据源</label>
                <select
                  id="remote-source"
                  value={remoteSource}
                  onChange={(e) => setRemoteSource(e.target.value as 'yfinance' | 'akshare')}
                >
                  <option value="yfinance">YFinance</option>
                  <option value="akshare">AkShare/A股</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="remote-symbol">symbol</label>
                <input
                  id="remote-symbol"
                  value={remoteSymbol}
                  onChange={(e) => setRemoteSymbol(e.target.value)}
                  placeholder={remoteSource === 'akshare' ? '如 600519 或 SH600519' : '如 GC=F 或 AAPL'}
                />
              </div>
              <div className="field">
                <label htmlFor="remote-timeframe">timeframe</label>
                <select
                  id="remote-timeframe"
                  value={remoteTimeframe}
                  onChange={(e) => setRemoteTimeframe(e.target.value)}
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
                <label htmlFor="remote-lookback">回看K线</label>
                <input
                  id="remote-lookback"
                  type="number"
                  min={10}
                  max={5000}
                  step={1}
                  value={remoteLookback}
                  onChange={(e) => setRemoteLookback(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="remote-adjust">复权</label>
                <select
                  id="remote-adjust"
                  value={remoteAdjust}
                  onChange={(e) => setRemoteAdjust(e.target.value as 'qfq' | 'hfq' | 'none')}
                >
                  <option value="qfq">前复权</option>
                  <option value="hfq">后复权</option>
                  <option value="none">不复权</option>
                </select>
              </div>
            </div>
            <div className="row" style={{ marginTop: 14 }}>
              <button className="button button-primary" onClick={handleRemoteImport} disabled={remoteMutation.isPending}>
                <Globe size={14} />
                {remoteMutation.isPending ? '下载中...' : '下载并导入'}
              </button>
              <span className="muted">使用公开行情接口，仅生成本地研究快照。</span>
            </div>
          </div>
        </div>
      </div>

      {pageError ? <div className="empty">错误：{pageError}</div> : null}
      {notice ? <div className="notice">{notice}</div> : null}

      <div className="panel">
        <div className="section-title">
          <Database size={15} />
          数据集列表
          {datasets.length ? <span className="tag">{selectedId ? '已选择数据集' : '未选择'}</span> : null}
        </div>
        {datasetsQuery.isPending ? (
          <div className="empty">加载中...</div>
        ) : datasetsQuery.isError ? (
          <div className="empty">数据集列表加载失败：{(datasetsQuery.error as Error).message}</div>
        ) : datasets.length === 0 ? (
          <div className="empty">暂无数据集。请先导入本地数据或生成演示数据。</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>symbol</th>
                <th>timeframe</th>
                <th>K线数</th>
                <th>起始 session</th>
                <th>结束 session</th>
                <th>创建时间</th>
                <th>dataset_id</th>
                <th>选择</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr key={dataset.id}>
                  <td className="code">{dataset.symbol}</td>
                  <td>{dataset.timeframe}</td>
                  <td>{dataset.bar_count}</td>
                  <td>{dataset.first_session}</td>
                  <td>{dataset.last_session}</td>
                  <td className="muted">{dataset.created_at}</td>
                  <td className="code">{dataset.id}</td>
                  <td>
                    {selectedId === dataset.id ? (
                      <span className="tag">当前选择</span>
                    ) : (
                      <button className="button" onClick={() => setSelectedId(dataset.id)}>选择</button>
                    )}
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

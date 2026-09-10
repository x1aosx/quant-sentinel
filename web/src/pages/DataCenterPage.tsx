import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Database, FileUp, Sparkles, Upload } from 'lucide-react';
import type { DatasetBar, DatasetSummary } from '../types';

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
  const [symbol, setSymbol] = useState('');
  const [timeframe, setTimeframe] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [notice, setNotice] = useState('');
  const [pageError, setPageError] = useState('');

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: listDatasets });
  const datasets = datasetsQuery.data?.items ?? [];

  const finishWith = (created: DatasetSummary, action: string) => {
    queryClient.invalidateQueries({ queryKey: ['datasets'] });
    setSelectedId(created.id);
    setNotice(`${action}成功：${created.symbol} ${created.timeframe}，${created.bar_count} 根K线，dataset_id=${created.id}`);
    setPageError('');
  };

  const uploadMutation = useMutation({
    mutationFn: (payload: { symbol: string; timeframe: string; bars: DatasetBar[] }) =>
      requestJson<DatasetSummary>('/datasets', { method: 'POST', body: JSON.stringify(payload) }),
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
      requestJson<DatasetSummary>('/datasets/sample', { method: 'POST', body: JSON.stringify(timeframe ? { timeframe } : {}) }),
    onSuccess: (created) => finishWith(created, '生成演示数据'),
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

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>数据中心</h1>
          <div className="muted">导入本地行情数据或生成演示数据集，供支撑阻力与价格行为分析使用。</div>
        </div>
        <span className="tag">{datasets.length} 个数据集</span>
      </div>

      <div className="grid grid-2">
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
            </div>
          </div>

          <div className="row" style={{ marginTop: 14 }}>
            <button className="button button-primary" onClick={handleUpload} disabled={uploadMutation.isPending}>
              <Upload size={14} />
              {uploadMutation.isPending ? '导入中...' : '解析并上传'}
            </button>
            <span className="muted">支持 CSV（session/date、OHLC、volume/tick_volume）与 JSON（bar 数组或 {'{bars: []}'}）。</span>
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

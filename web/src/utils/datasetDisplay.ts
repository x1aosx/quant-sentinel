import type { DatasetSummary } from '../types';

export const DEFAULT_ANALYSIS_TIMEFRAME = '1d';
export const DEFAULT_REALTIME_TIMEFRAME = '15m';
export const REALTIME_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h'] as const;

const TIMEFRAME_LABELS: Record<string, string> = {
  '1m': '1分钟',
  '5m': '5分钟',
  '15m': '15分钟',
  '30m': '30分钟',
  '1h': '1小时',
  '1d': '日线',
  '1w': '周线',
};

export function formatTimeframeLabel(value: string): string {
  return TIMEFRAME_LABELS[value] ?? value;
}

export interface StockDatasetGroup {
  symbol: string;
  title: string;
  datasets: DatasetSummary[];
}

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

function timeframeOrder(value: string): number {
  const index = REALTIME_TIMEFRAMES.indexOf(value as (typeof REALTIME_TIMEFRAMES)[number]);
  if (index >= 0) return index;
  if (value === '4h') return REALTIME_TIMEFRAMES.length;
  if (value === '1d') return REALTIME_TIMEFRAMES.length + 1;
  if (value === '1w') return REALTIME_TIMEFRAMES.length + 2;
  return REALTIME_TIMEFRAMES.length + 3;
}

export function groupDatasetsByStock(datasets: DatasetSummary[]): StockDatasetGroup[] {
  const groups: StockDatasetGroup[] = [];
  const groupBySymbol = new Map<string, StockDatasetGroup>();

  datasets.forEach((dataset) => {
    const normalized = normalizeSymbol(dataset.symbol);
    const key = normalized || `dataset:${dataset.id}`;
    let group = groupBySymbol.get(key);
    if (!group) {
      group = {
        symbol: dataset.symbol.trim(),
        title: dataset.title?.trim() || dataset.symbol.trim(),
        datasets: [],
      };
      groupBySymbol.set(key, group);
      groups.push(group);
    }
    if ((!group.title || group.title === group.symbol) && dataset.title?.trim()) {
      group.title = dataset.title.trim();
    }
    group.datasets.push(dataset);
  });

  groups.forEach((group) => {
    group.datasets.sort((left, right) => {
      const order = timeframeOrder(left.timeframe) - timeframeOrder(right.timeframe);
      return order || left.timeframe.localeCompare(right.timeframe);
    });
  });
  return groups;
}

export function findStockGroup(
  groups: StockDatasetGroup[],
  symbol: string,
): StockDatasetGroup | undefined {
  const normalized = normalizeSymbol(symbol);
  return groups.find((group) => normalizeSymbol(group.symbol) === normalized);
}

export function datasetForTimeframe(
  group: StockDatasetGroup | undefined,
  timeframe: string,
): DatasetSummary | undefined {
  const normalized = timeframe.trim().toLowerCase();
  return group?.datasets.find(
    (dataset) => dataset.timeframe.trim().toLowerCase() === normalized,
  );
}

export function preferredDataset(
  group: StockDatasetGroup | undefined,
  timeframe = DEFAULT_ANALYSIS_TIMEFRAME,
): DatasetSummary | undefined {
  return (
    datasetForTimeframe(group, timeframe) ||
    datasetForTimeframe(group, DEFAULT_ANALYSIS_TIMEFRAME) ||
    group?.datasets[0]
  );
}

function formatCompactSessionTime(value: string): string | null {
  const match = /^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?)?$/.exec(value);
  if (!match) return null;

  const [, year, month, day, hour, minute, second] = match;
  const date = `${year}-${month}-${day}`;
  if (!hour) return date;

  const time = second ? `${hour}:${minute}:${second}` : `${hour}:${minute}`;
  return `${date} ${time}`;
}

export function formatSessionTime(value?: string): string {
  if (!value) return '--';
  const normalized = value.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return normalized;

  const compactTime = formatCompactSessionTime(normalized);
  if (compactTime) return compactTime;

  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return normalized;

  const pad = (part: number): string => String(part).padStart(2, '0');
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join('-') + ` ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

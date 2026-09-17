export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

export function formatNumber(
  value: number | null | undefined,
  digits = 4,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return value.toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function formatPercent(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatCompact(value: unknown): string {
  if (value === null || value === undefined || value === '') return '--';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '--';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(formatCompact).join(' / ');
  return JSON.stringify(value);
}

export function shortId(value?: string | null, length = 10): string {
  if (!value) return '--';
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

export function progressValue(current?: number, total?: number): number {
  if (!total || total <= 0 || current === undefined) return 0;
  return Math.max(0, Math.min(100, (current / total) * 100));
}

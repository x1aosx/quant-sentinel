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

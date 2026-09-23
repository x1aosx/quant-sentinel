import type { Time, UTCTimestamp } from 'lightweight-charts';

/**
 * K 线时间归一化：把后端各式各样的 `session_id` 转成 lightweight-charts 能接受的时间。
 *
 * 解析口径与后端 `xquant.registry.bar_utils.parse_session_time` 保持一致：
 * - ISO 字符串带时区按原时区解析，不带时区按 UTC 处理；
 * - 紧凑格式 `YYYYMMDDHHMMSS` / `YYYYMMDDHHMM` / `YYYYMMDD` 一律按 UTC 处理。
 *
 * lightweight-charts 用第一条数据决定整条序列的时间类型，同一份数据里混用
 * BusinessDay 字符串和 UTCTimestamp 数字会直接抛
 * `time must be of type isUTCTimestamp`，所以这里在数据集级别统一类型：
 * 全部是日期时用 BusinessDay，只要有一条带时间就全部转成 UTCTimestamp。
 */
export type SessionTime =
  | { readonly kind: 'date'; readonly year: number; readonly month: number; readonly day: number }
  | { readonly kind: 'instant'; readonly seconds: number };

const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const COMPACT_PATTERN = /^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(?:(\d{2}))?)?$/;
const EPOCH_SECONDS_PATTERN = /^\d{10}$/;
const EPOCH_MILLIS_PATTERN = /^\d{13}$/;
const NAIVE_DATETIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$/;

/** 无法解析的 session_id 的兜底起点（保持旧的占位行为）。 */
const FALLBACK_TIME_BASE = 0;

function isValidDate(year: number, month: number, day: number): boolean {
  return year >= 1000 && year <= 9999 && month >= 1 && month <= 12 && day >= 1 && day <= 31;
}

function utcSeconds(
  year: number,
  month: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
): number | null {
  if (!isValidDate(year, month, day)) return null;
  if (hour > 23 || minute > 59 || second > 59) return null;
  return Math.floor(Date.UTC(year, month - 1, day, hour, minute, second) / 1000);
}

export function parseSessionTime(sessionId: string): SessionTime | null {
  const text = String(sessionId ?? '').trim();
  if (!text) return null;

  const dateOnly = DATE_ONLY_PATTERN.exec(text);
  if (dateOnly) {
    const year = Number(dateOnly[1]);
    const month = Number(dateOnly[2]);
    const day = Number(dateOnly[3]);
    return isValidDate(year, month, day) ? { kind: 'date', year, month, day } : null;
  }

  if (EPOCH_SECONDS_PATTERN.test(text)) {
    return { kind: 'instant', seconds: Number(text) };
  }
  if (EPOCH_MILLIS_PATTERN.test(text)) {
    return { kind: 'instant', seconds: Math.floor(Number(text) / 1000) };
  }

  const compact = COMPACT_PATTERN.exec(text);
  if (compact) {
    const year = Number(compact[1]);
    const month = Number(compact[2]);
    const day = Number(compact[3]);
    if (!isValidDate(year, month, day)) return null;
    if (compact[4] === undefined) return { kind: 'date', year, month, day };

    const seconds = utcSeconds(
      year,
      month,
      day,
      Number(compact[4]),
      Number(compact[5]),
      compact[6] === undefined ? 0 : Number(compact[6]),
    );
    return seconds === null ? null : { kind: 'instant', seconds };
  }

  const naive = NAIVE_DATETIME_PATTERN.exec(text);
  if (naive) {
    const seconds = utcSeconds(
      Number(naive[1]),
      Number(naive[2]),
      Number(naive[3]),
      Number(naive[4]),
      Number(naive[5]),
      naive[6] === undefined ? 0 : Number(naive[6]),
    );
    return seconds === null ? null : { kind: 'instant', seconds };
  }

  const timestamp = Date.parse(text);
  return Number.isFinite(timestamp) ? { kind: 'instant', seconds: Math.floor(timestamp / 1000) } : null;
}

export function sessionTimeToChartTime(time: SessionTime): Time {
  if (time.kind === 'instant') return time.seconds as UTCTimestamp;
  return `${String(time.year).padStart(4, '0')}-${String(time.month).padStart(2, '0')}-${String(
    time.day,
  ).padStart(2, '0')}`;
}

function resolveSeconds(time: SessionTime | null, previousSeconds: number | null, index: number): number {
  if (time?.kind === 'instant') return time.seconds;
  if (time?.kind === 'date') {
    return Math.floor(Date.UTC(time.year, time.month - 1, time.day) / 1000);
  }
  // 解析不了的 session_id（如占位符 S0001）继续按顺序占位，保证类型一致且不丢数据。
  return previousSeconds === null ? FALLBACK_TIME_BASE + index : previousSeconds + 1;
}

/**
 * 把整份数据的 session_id 转成同一类型的时间序列。
 * 只有全部是日期时返回 BusinessDay 字符串，否则统一返回 UTCTimestamp 数字。
 */
export function buildChartTimes(sessionIds: readonly string[]): Time[] {
  const parsed = sessionIds.map((sessionId) => parseSessionTime(sessionId));
  if (parsed.length > 0 && parsed.every((item) => item?.kind === 'date')) {
    return parsed.map((item) => sessionTimeToChartTime(item as SessionTime));
  }

  let previousSeconds: number | null = null;
  return parsed.map((item, index) => {
    const seconds = resolveSeconds(item, previousSeconds, index);
    previousSeconds = seconds;
    return seconds as UTCTimestamp;
  });
}

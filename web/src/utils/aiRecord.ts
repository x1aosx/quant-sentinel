import type { AIAnalysisRecord } from '../types';

export function decisionOf(record?: AIAnalysisRecord | null): Record<string, any> {
  const stage2 = record?.stage2_decision ?? {};
  const decision = stage2.decision;
  return (decision && typeof decision === 'object' ? decision : stage2) as Record<string, any>;
}

export function diagnosisOf(record?: AIAnalysisRecord | null): Record<string, any> {
  return (record?.stage1_diagnosis ?? {}) as Record<string, any>;
}

export function actionOf(record?: AIAnalysisRecord | null): string {
  const decision = decisionOf(record);
  const value = decision.action ?? decision.order_type ?? decision.order_direction ?? 'WAIT';
  const normalized = String(value).toUpperCase();
  if (['BUY', 'LONG', '做多'].includes(normalized)) return 'LONG';
  if (['SELL', 'SHORT', '做空'].includes(normalized)) return 'SHORT';
  if (['HOLD', 'NONE', 'WAIT', '不下单', '观望'].includes(normalized)) return 'WAIT';
  return normalized;
}

export function confidenceOf(record?: AIAnalysisRecord | null): number {
  const value = decisionOf(record).confidence ?? decisionOf(record).trade_confidence ?? 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatValue(value: unknown, fallback = '--'): string {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

// 模型偶尔把本该是文本的字段写成对象（例如 next_cycle 返回带 expected/transition 的字典），
// 直接渲染进 JSX 会触发 React #31（Objects are not valid as a React child），
// 因此这里统一压成一行可读文本。
const TEXT_HINT_KEYS = [
  'label',
  'title',
  'phase',
  'summary',
  'description',
  'detail',
  'details',
  'expected',
  'expected_cycle',
  'cycle',
  'state',
  'direction',
  'bias',
  'outlook',
  'base_case',
  'current',
  'overview',
  'overall',
  'status',
  'conclusion',
  'reasoning',
  'note',
  'text',
  'value',
  'answer',
  'result',
  'name',
  'key',
  'scenario',
];

export function describeValue(value: unknown, fallback = '--'): string {
  return describeText(value, 3) || fallback;
}

// 统计卡片只放得下一行短标签，优先取紧凑的状态字段，长散文兜底并截断。
const COMPACT_HINT_KEYS = [
  'phase',
  'label',
  'title',
  'name',
  'cycle',
  'state',
  'expected',
  'expected_cycle',
  'direction',
  'bias',
  'status',
  'text',
  'value',
  'key',
  'scenario',
  'summary',
  'description',
];

export function compactLabel(value: unknown, fallback = '--', maxLength = 48): string {
  const text = compactText(value, 3) || fallback;
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

function compactText(value: unknown, depth: number): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (depth < 0) return '';
  if (Array.isArray(value)) {
    for (const item of value) {
      const text = compactText(item, depth - 1);
      if (text) return text;
    }
    return '';
  }
  const record = value as Record<string, unknown>;
  for (const key of COMPACT_HINT_KEYS) {
    const text = compactText(record[key], depth - 1);
    if (text) return text;
  }
  return describeText(value, depth);
}

function describeText(value: unknown, depth: number): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (depth < 0) return '';
  if (Array.isArray(value)) {
    for (const item of value) {
      const text = describeText(item, depth - 1);
      if (text) return text;
    }
    return '';
  }
  const record = value as Record<string, unknown>;
  for (const key of TEXT_HINT_KEYS) {
    const text = describeText(record[key], depth - 1);
    if (text) return text;
  }
  // 没有命中已知键时取最长的标量文本：模型常用散文说明状态。
  let longest = '';
  for (const item of Object.values(record)) {
    if (typeof item !== 'string' && typeof item !== 'number') continue;
    const text = String(item).trim();
    if (text.length > longest.length) longest = text;
  }
  return longest;
}

/** 把数组/对象字段渲染成一行文本，对象元素不再输出 [object Object]。 */
export function listText(value: unknown, fallback = '--'): string {
  const items = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
  const texts = items.map((item) => describeText(item, 3)).filter(Boolean);
  return texts.length ? texts.join('、') : fallback;
}

export function probabilityRows(value: unknown): Array<{ label: string; probability: number }> {
  const entries: Array<{ label: string; probability: number }> = [];
  if (Array.isArray(value)) {
    value.forEach((entry, index) => {
      if (entry && typeof entry === 'object') {
        const item = entry as Record<string, unknown>;
        const label = firstText(
          item.label,
          item.name,
          item.scenario,
          item.key,
          item.title,
          item.direction,
        );
        const probability = toNumber(
          item.probability ?? item.prob ?? item.value ?? item.weight ?? item.p,
        );
        if (probability === null) return;
        entries.push({ label: label || `情景 ${index + 1}`, probability });
        return;
      }
      const probability = toNumber(entry);
      if (probability === null) return;
      entries.push({ label: `情景 ${index + 1}`, probability });
    });
  } else if (value && typeof value === 'object') {
    Object.entries(value as Record<string, unknown>).forEach(([label, raw]) => {
      const probability = toNumber(raw);
      if (probability === null) return;
      entries.push({ label, probability });
    });
  }
  if (entries.length === 0) return [];
  // 模型经常用 0~1 的小数给概率，这里统一换算成百分比。
  const total = entries.reduce((sum, item) => sum + item.probability, 0);
  const looksFractional =
    total > 0 &&
    total <= 1.05 &&
    entries.every((item) => item.probability >= 0 && item.probability <= 1);
  return entries
    .map((item) => ({
      label: item.label,
      probability: Math.round((looksFractional ? item.probability * 100 : item.probability) * 100) / 100,
    }))
    .sort((left, right) => right.probability - left.probability);
}

export function firstText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return '';
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const parsed = Number(value.trim().replace(/%$/, ''));
  return Number.isFinite(parsed) ? parsed : null;
}

export function responseText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  const record = value as Record<string, any>;
  if (typeof record.content === 'string') return record.content;
  return JSON.stringify(value, null, 2);
}

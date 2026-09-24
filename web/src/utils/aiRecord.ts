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

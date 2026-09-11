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
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value as Record<string, unknown>)
    .map(([label, probability]) => ({ label, probability: Number(probability) || 0 }))
    .sort((left, right) => right.probability - left.probability);
}

export function responseText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  const record = value as Record<string, any>;
  if (typeof record.content === 'string') return record.content;
  return JSON.stringify(value, null, 2);
}

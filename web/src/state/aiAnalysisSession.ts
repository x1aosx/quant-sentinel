import { useSyncExternalStore } from 'react';
import type { AIAnalysisRecord, BatchAnalyzeResponse } from '../types';

export type AIAnalysisMode = 'single' | 'monitor';
export type AIAnalysisViewKey =
  | 'live'
  | 'diagnosis'
  | 'decision'
  | 'visualization'
  | 'future'
  | 'raw'
  | 'debug';

export interface AIAnalysisSessionState {
  mode: AIAnalysisMode;
  view: AIAnalysisViewKey;
  datasetId: string;
  record: AIAnalysisRecord | null;
  streamLog: string;
  running: boolean;
  notice: string;
  error: string;
  question: string;
  lastBatch: BatchAnalyzeResponse | null;
}

const STORAGE_KEY = 'xquant.ai-analysis-session.v1';
const MAX_STREAM_LOG_LENGTH = 160_000;
const VIEW_KEYS = new Set<AIAnalysisViewKey>([
  'live',
  'diagnosis',
  'decision',
  'visualization',
  'future',
  'raw',
  'debug',
]);

const DEFAULT_STATE: AIAnalysisSessionState = {
  mode: 'single',
  view: 'live',
  datasetId: '',
  record: null,
  streamLog: '',
  running: false,
  notice: '',
  error: '',
  question: '',
  lastBatch: null,
};

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function restoreState(): AIAnalysisSessionState {
  if (typeof window === 'undefined') return DEFAULT_STATE;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_STATE;
    const value = JSON.parse(raw) as Record<string, unknown>;
    return {
      mode: value.mode === 'monitor' ? 'monitor' : 'single',
      view: VIEW_KEYS.has(value.view as AIAnalysisViewKey)
        ? (value.view as AIAnalysisViewKey)
        : 'live',
      datasetId: typeof value.datasetId === 'string' ? value.datasetId : '',
      record: isObject(value.record) ? (value.record as AIAnalysisRecord) : null,
      streamLog: typeof value.streamLog === 'string' ? value.streamLog : '',
      running: value.running === true,
      notice: typeof value.notice === 'string' ? value.notice : '',
      error: typeof value.error === 'string' ? value.error : '',
      question: typeof value.question === 'string' ? value.question : '',
      lastBatch: isObject(value.lastBatch)
        ? (value.lastBatch as BatchAnalyzeResponse)
        : null,
    };
  } catch {
    return DEFAULT_STATE;
  }
}

let state = restoreState();
let activeRequest: AbortController | null = null;
let persistTimer: number | null = null;
let pageUnloading = false;
const listeners = new Set<() => void>();

function trimStreamLog(value: string): string {
  if (value.length <= MAX_STREAM_LOG_LENGTH) return value;
  return `[历史日志已截断]\n${value.slice(-MAX_STREAM_LOG_LENGTH)}`;
}

function persistNow() {
  if (typeof window === 'undefined') return;
  if (persistTimer !== null) {
    window.clearTimeout(persistTimer);
    persistTimer = null;
  }
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Session storage can be unavailable or full. The in-memory state remains usable.
  }
}

function schedulePersist() {
  if (typeof window === 'undefined' || persistTimer !== null) return;
  persistTimer = window.setTimeout(() => {
    persistTimer = null;
    persistNow();
  }, 120);
}

export function updateAIAnalysisSession(
  update:
    | Partial<AIAnalysisSessionState>
    | ((current: AIAnalysisSessionState) => Partial<AIAnalysisSessionState>),
) {
  const patch = typeof update === 'function' ? update(state) : update;
  state = {
    ...state,
    ...patch,
  };
  if (patch.streamLog !== undefined) {
    state.streamLog = trimStreamLog(state.streamLog);
  }
  schedulePersist();
  listeners.forEach((listener) => listener());
}

export function subscribeAIAnalysisSession(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAIAnalysisSessionSnapshot() {
  return state;
}

export function useAIAnalysisSession() {
  return useSyncExternalStore(
    subscribeAIAnalysisSession,
    getAIAnalysisSessionSnapshot,
    getAIAnalysisSessionSnapshot,
  );
}

export function beginAIAnalysisRequest(): AbortController | null {
  if (activeRequest) return null;
  activeRequest = new AbortController();
  return activeRequest;
}

export function finishAIAnalysisRequest(controller: AbortController) {
  if (activeRequest === controller) {
    activeRequest = null;
  }
}

export function hasActiveAIAnalysisRequest() {
  return activeRequest !== null;
}

export function isAIAnalysisPageUnloading() {
  return pageUnloading;
}

if (typeof window !== 'undefined') {
  const markPageUnloading = () => {
    pageUnloading = true;
    persistNow();
  };
  window.addEventListener('beforeunload', markPageUnloading);
  window.addEventListener('pagehide', markPageUnloading);
  window.addEventListener('pageshow', () => {
    pageUnloading = false;
  });
}

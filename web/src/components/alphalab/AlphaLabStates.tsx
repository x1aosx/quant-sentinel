import type { ReactNode } from 'react';
import {
  AlertTriangle,
  Inbox,
  Loader2,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';

export function AlphaLabLoading({ label = '加载中' }: { label?: string }) {
  return (
    <div className="alphalab-state" role="status">
      <Loader2 className="alphalab-spin" size={18} />
      <span>{label}</span>
    </div>
  );
}

export function AlphaLabError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="alphalab-state alphalab-state-error" role="alert">
      <AlertTriangle size={18} />
      <span>{message}</span>
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          <RefreshCw size={14} />
          重试
        </button>
      ) : null}
    </div>
  );
}

export function AlphaLabEmpty({
  children = '暂无数据',
}: {
  children?: ReactNode;
}) {
  return (
    <div className="alphalab-state alphalab-state-empty">
      <Inbox size={18} />
      <span>{children}</span>
    </div>
  );
}

export function AlphaLabQueryStatus({
  isFetching,
  isError,
  updatedAt,
  onRefresh,
}: {
  isFetching: boolean;
  isError: boolean;
  updatedAt?: number;
  onRefresh: () => void;
}) {
  const updatedLabel = updatedAt
    ? new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date(updatedAt))
    : '--';
  return (
    <div className="alphalab-query-status">
      <span className={isError ? 'alphalab-status-dot error' : 'alphalab-status-dot'} />
      <span>{isFetching ? '刷新中' : `更新 ${updatedLabel}`}</span>
      <button
        type="button"
        className="button"
        onClick={onRefresh}
        disabled={isFetching}
      >
        <RefreshCw className={isFetching ? 'alphalab-spin' : undefined} size={14} />
        刷新
      </button>
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  PENDING: '待处理',
  QUEUED: '排队中',
  RUNNING: '运行中',
  SUCCEEDED: '成功',
  SUCCESS: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
  STOPPED: '已停止',
  PAUSED: '已暂停',
  CANDIDATE: '候选',
  VALIDATED: '已验证',
  PRODUCTION: '生产中',
  DEPRECATED: '已弃用',
  REJECTED: '已拒绝',
};

function statusTone(status: string): string {
  switch (status.toUpperCase()) {
    case 'RUNNING':
      return 'running';
    case 'SUCCEEDED':
    case 'SUCCESS':
    case 'VALIDATED':
    case 'PRODUCTION':
      return 'ok';
    case 'FAILED':
    case 'REJECTED':
    case 'CANCELLED':
    case 'STOPPED':
      return 'danger';
    case 'PENDING':
    case 'QUEUED':
    case 'PAUSED':
    case 'CANDIDATE':
      return 'warning';
    default:
      return 'neutral';
  }
}

export function AlphaLabStatus({ status }: { status?: string | null }) {
  const value = status || 'UNKNOWN';
  const tone = statusTone(value);
  return (
    <span className={`badge alphalab-status-${tone}`}>
      {tone === 'ok' ? <ShieldCheck size={12} /> : null}
      {STATUS_LABELS[value.toUpperCase()] ?? value}
    </span>
  );
}

export function AlphaLabMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="alphalab-metric">
      <div className="stat-label">{label}</div>
      <div className="alphalab-metric-value">{value}</div>
      {detail ? <div className="alphalab-metric-detail">{detail}</div> : null}
    </div>
  );
}

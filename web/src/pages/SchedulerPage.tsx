import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  Clock3,
  GitBranch,
  History,
  ListChecks,
  Loader2,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ServerCog,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  ScheduleDefinition,
  SchedulerMisfirePolicy,
  SchedulerTrigger,
  TaskDefinition,
  TaskExecution,
  WorkerSummary,
} from '../types';

type SchedulerTab = 'plans' | 'executions' | 'workers';
type TriggerType = 'cron' | 'interval' | 'fixed_delay' | 'date';

interface ScheduleFormState {
  id: string;
  taskName: string;
  triggerType: TriggerType;
  minute: string;
  hour: string;
  day: string;
  month: string;
  dayOfWeek: string;
  intervalSeconds: string;
  delaySeconds: string;
  startAt: string;
  runAt: string;
  params: string;
  timezone: string;
  misfirePolicy: SchedulerMisfirePolicy;
  enabled: boolean;
  maxCatchUpRuns: string;
}

interface SchedulePayload {
  id: string;
  task_name: string;
  trigger: SchedulerTrigger;
  params: Record<string, unknown>;
  timezone: string;
  misfire_policy: SchedulerMisfirePolicy;
  enabled: boolean;
  max_catch_up_runs: number;
}

interface DagNode {
  id: string;
  taskName: string;
  status: string;
  role: 'dependency' | 'selected' | 'child';
  x: number;
  y: number;
}

const DEFAULT_FORM: ScheduleFormState = {
  id: '',
  taskName: '',
  triggerType: 'cron',
  minute: '30',
  hour: '9',
  day: '*',
  month: '*',
  dayOfWeek: '1-5',
  intervalSeconds: '3600',
  delaySeconds: '60',
  startAt: '',
  runAt: '',
  params: '{}',
  timezone: 'Asia/Shanghai',
  misfirePolicy: 'FIRE_ONCE',
  enabled: true,
  maxCatchUpRuns: '30',
};

const EXECUTION_STATUS_LABELS: Record<string, string> = {
  PENDING: '待处理',
  WAITING: '等待依赖',
  QUEUED: '排队中',
  RUNNING: '执行中',
  SUCCESS: '成功',
  FAILED: '失败',
  RETRYING: '重试中',
  TIMEOUT: '超时',
  CANCELLED: '已取消',
  SKIPPED: '已跳过',
};

const RETRYABLE_STATUSES = new Set([
  'FAILED',
  'TIMEOUT',
  'CANCELLED',
  'SKIPPED',
]);

const ACTIVE_STATUSES = new Set(['PENDING', 'WAITING', 'QUEUED', 'RUNNING', 'RETRYING']);

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function shortId(value: string, length = 8): string {
  return value.length > length ? `${value.slice(0, length)}...` : value;
}

function formatDateTime(value?: string | null): string {
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

function formatFullDateTime(value?: string | null): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

function toDateTimeLocal(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function durationLabel(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(2)} s`;
  if (value < 3_600_000) return `${(value / 60_000).toFixed(1)} min`;
  return `${(value / 3_600_000).toFixed(1)} h`;
}

function intervalLabel(seconds: number): string {
  if (seconds < 60) return `每 ${seconds} 秒`;
  if (seconds < 3600) return `每 ${(seconds / 60).toFixed(seconds % 60 ? 1 : 0)} 分钟`;
  if (seconds < 86_400) return `每 ${(seconds / 3600).toFixed(seconds % 3600 ? 1 : 0)} 小时`;
  return `每 ${(seconds / 86_400).toFixed(seconds % 86_400 ? 1 : 0)} 天`;
}

function triggerSummary(trigger: SchedulerTrigger): string {
  if (trigger.type === 'cron') {
    return `Cron ${trigger.minute} ${trigger.hour} ${trigger.day} ${trigger.month} ${trigger.day_of_week}`;
  }
  if (trigger.type === 'interval') {
    return intervalLabel(Number(trigger.interval_seconds));
  }
  if (trigger.type === 'fixed_delay') {
    return `延迟 ${intervalLabel(Number(trigger.delay_seconds))}`;
  }
  return `单次 ${formatFullDateTime(trigger.run_at)}`;
}

function statusTone(status: string): string {
  switch (status) {
    case 'SUCCESS':
      return 'ok';
    case 'FAILED':
    case 'TIMEOUT':
    case 'CANCELLED':
      return 'danger';
    case 'RUNNING':
      return 'running';
    case 'RETRYING':
    case 'WAITING':
      return 'warning';
    case 'PENDING':
    case 'QUEUED':
      return 'info';
    default:
      return 'neutral';
  }
}

function statusLabel(status: string): string {
  return EXECUTION_STATUS_LABELS[status] ?? status;
}

function parseParams(value: string): Record<string, unknown> {
  const text = value.trim();
  if (!text) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('params 必须是合法的 JSON');
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('params 必须是 JSON 对象');
  }
  return parsed as Record<string, unknown>;
}

function toIsoDateTime(value: string, label: string): string {
  const date = new Date(value);
  if (!value.trim() || Number.isNaN(date.getTime())) {
    throw new Error(`${label} 不是有效时间`);
  }
  return date.toISOString();
}

function buildSchedulePayload(form: ScheduleFormState): SchedulePayload {
  const timezone = form.timezone.trim() || 'Asia/Shanghai';
  const maxCatchUpRuns = Number(form.maxCatchUpRuns);
  if (!form.id.trim()) throw new Error('计划 ID 不能为空');
  if (!form.taskName.trim()) throw new Error('请选择任务');
  if (!Number.isInteger(maxCatchUpRuns) || maxCatchUpRuns < 0) {
    throw new Error('补跑次数必须是非负整数');
  }

  let trigger: SchedulerTrigger;
  if (form.triggerType === 'cron') {
    trigger = {
      type: 'cron',
      minute: form.minute.trim() || '*',
      hour: form.hour.trim() || '*',
      day: form.day.trim() || '*',
      month: form.month.trim() || '*',
      day_of_week: form.dayOfWeek.trim() || '*',
      timezone,
    };
  } else if (form.triggerType === 'interval') {
    const intervalSeconds = Number(form.intervalSeconds);
    if (!Number.isFinite(intervalSeconds) || intervalSeconds <= 0) {
      throw new Error('间隔秒数必须大于 0');
    }
    trigger = {
      type: 'interval',
      interval_seconds: intervalSeconds,
      start_at: form.startAt ? toIsoDateTime(form.startAt, '开始时间') : null,
      timezone,
    };
  } else if (form.triggerType === 'fixed_delay') {
    const delaySeconds = Number(form.delaySeconds);
    if (!Number.isFinite(delaySeconds) || delaySeconds <= 0) {
      throw new Error('延迟秒数必须大于 0');
    }
    trigger = {
      type: 'fixed_delay',
      delay_seconds: delaySeconds,
      start_at: form.startAt ? toIsoDateTime(form.startAt, '开始时间') : null,
      timezone,
    };
  } else {
    trigger = {
      type: 'date',
      run_at: toIsoDateTime(form.runAt, '执行时间'),
      timezone,
    };
  }

  return {
    id: form.id.trim(),
    task_name: form.taskName.trim(),
    trigger,
    params: parseParams(form.params),
    timezone,
    misfire_policy: form.misfirePolicy,
    enabled: form.enabled,
    max_catch_up_runs: maxCatchUpRuns,
  };
}

function formFromSchedule(schedule: ScheduleDefinition): ScheduleFormState {
  const base: ScheduleFormState = {
    ...DEFAULT_FORM,
    id: schedule.id,
    taskName: schedule.task_name,
    params: JSON.stringify(schedule.params ?? {}, null, 2),
    timezone: schedule.timezone,
    misfirePolicy: schedule.misfire_policy,
    enabled: schedule.enabled,
    maxCatchUpRuns: String(schedule.max_catch_up_runs),
  };
  const trigger = schedule.trigger;
  if (trigger.type === 'cron') {
    return {
      ...base,
      triggerType: 'cron',
      minute: trigger.minute,
      hour: trigger.hour,
      day: trigger.day,
      month: trigger.month,
      dayOfWeek: trigger.day_of_week,
    };
  }
  if (trigger.type === 'interval') {
    return {
      ...base,
      triggerType: 'interval',
      intervalSeconds: String(trigger.interval_seconds),
      startAt: toDateTimeLocal(trigger.start_at),
    };
  }
  if (trigger.type === 'fixed_delay') {
    return {
      ...base,
      triggerType: 'fixed_delay',
      delaySeconds: String(trigger.delay_seconds),
      startAt: toDateTimeLocal(trigger.start_at),
    };
  }
  return {
    ...base,
    triggerType: 'date',
    runAt: toDateTimeLocal(trigger.run_at),
  };
}

function connectionPath(from: DagNode, to: DagNode): string {
  const fromX = from.x + 210;
  const fromY = from.y + 30;
  const toX = to.x;
  const toY = to.y + 30;
  const middle = (fromX + toX) / 2;
  return `M ${fromX} ${fromY} C ${middle} ${fromY}, ${middle} ${toY}, ${toX} ${toY}`;
}

function DependencyGraph({
  selected,
  executions,
}: {
  selected: TaskExecution;
  executions: TaskExecution[];
}) {
  const graph = useMemo(() => {
    const executionMap = new Map(executions.map((execution) => [execution.id, execution]));
    executionMap.set(selected.id, selected);

    const dependencyIds = Array.from(new Set(selected.depends_on ?? []));
    const childIds = Array.from(
      new Set(
        executions
          .filter(
            (execution) =>
              execution.id !== selected.id &&
              (execution.parent_execution_id === selected.id ||
                (execution.depends_on ?? []).includes(selected.id)),
          )
          .map((execution) => execution.id),
      ),
    );

    if (!dependencyIds.length && !childIds.length) return null;

    const rowCount = Math.max(dependencyIds.length, childIds.length, 1);
    const rowHeight = 82;
    const height = Math.max(168, rowCount * rowHeight + 36);
    const centerY = 36 + ((rowCount - 1) * rowHeight) / 2;
    const nodeFor = (
      id: string,
      role: DagNode['role'],
      x: number,
      y: number,
    ): DagNode => {
      const execution = executionMap.get(id);
      return {
        id,
        taskName: execution?.task_name ?? '未知执行',
        status: execution?.status ?? 'WAITING',
        role,
        x,
        y,
      };
    };
    const dependencies = dependencyIds.map((id, index) =>
      nodeFor(id, 'dependency', 30, 36 + index * rowHeight),
    );
    const selectedNode = nodeFor(selected.id, 'selected', 335, centerY);
    const children = childIds.map((id, index) =>
      nodeFor(id, 'child', 640, 36 + index * rowHeight),
    );

    return {
      width: 880,
      height,
      nodes: [...dependencies, selectedNode, ...children],
      edges: [
        ...dependencies.map((node) => ({ from: node, to: selectedNode })),
        ...children.map((node) => ({ from: selectedNode, to: node })),
      ],
    };
  }, [executions, selected]);

  if (!graph) {
    return <div className="empty">该执行没有父级依赖或下游子任务。</div>;
  }

  return (
    <div className="scheduler-dag-canvas">
      <svg viewBox={`0 0 ${graph.width} ${graph.height}`} role="img" aria-label="执行依赖 DAG">
        <defs>
          <marker
            id="scheduler-dag-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="scheduler-dag-arrow" />
          </marker>
        </defs>
        {graph.edges.map((edge, index) => (
          <path
            key={`${edge.from.id}-${edge.to.id}-${index}`}
            d={connectionPath(edge.from, edge.to)}
            className="scheduler-dag-edge"
            markerEnd="url(#scheduler-dag-arrow)"
          />
        ))}
        {graph.nodes.map((node) => (
          <g
            key={`${node.role}-${node.id}`}
            className={`scheduler-dag-node ${statusTone(node.status)}${
              node.role === 'selected' ? ' selected' : ''
            }`}
          >
            <title>
              {node.taskName} · {statusLabel(node.status)} · {node.id}
            </title>
            <rect x={node.x} y={node.y} width="210" height="60" rx="7" />
            <text x={node.x + 12} y={node.y + 24} className="scheduler-dag-node-title">
              {shortId(node.taskName, 25)}
            </text>
            <text x={node.x + 12} y={node.y + 44} className="scheduler-dag-node-meta">
              {statusLabel(node.status)} · {shortId(node.id, 10)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function SchedulerPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<SchedulerTab>('plans');
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ScheduleFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const [executionError, setExecutionError] = useState('');
  const [executionFilter, setExecutionFilter] = useState('');
  const [selectedExecutionId, setSelectedExecutionId] = useState('');
  const [manualTaskName, setManualTaskName] = useState('');
  const [manualParams, setManualParams] = useState('{}');
  const [manualPriority, setManualPriority] = useState('5');

  const tasksQuery = useQuery({
    queryKey: ['scheduler', 'tasks'],
    queryFn: api.listSchedulerTasks,
    refetchInterval: 15_000,
  });
  const schedulesQuery = useQuery({
    queryKey: ['scheduler', 'schedules'],
    queryFn: () => api.listSchedules(),
    refetchInterval: 10_000,
  });
  const executionsQuery = useQuery({
    queryKey: ['scheduler', 'executions', executionFilter],
    queryFn: () =>
      api.listExecutions({
        task_name: executionFilter || undefined,
        limit: 100,
      }),
    refetchInterval: 4000,
  });
  const workersQuery = useQuery({
    queryKey: ['scheduler', 'workers'],
    queryFn: api.listSchedulerWorkers,
    refetchInterval: 5000,
  });
  const executionDetailQuery = useQuery({
    queryKey: ['scheduler', 'execution', selectedExecutionId],
    queryFn: () => api.getExecution(selectedExecutionId),
    enabled: Boolean(selectedExecutionId),
    refetchInterval: selectedExecutionId ? 4000 : false,
  });

  const tasks = tasksQuery.data ?? [];
  const schedules = schedulesQuery.data ?? [];
  const executions = executionsQuery.data ?? [];
  const workers = workersQuery.data ?? [];
  const selectedExecution =
    executionDetailQuery.data ??
    executions.find((execution) => execution.id === selectedExecutionId);

  const enabledSchedules = schedules.filter((schedule) => schedule.enabled).length;
  const activeExecutions = executions.filter((execution) =>
    ACTIVE_STATUSES.has(execution.status),
  ).length;
  const failedExecutions = executions.filter((execution) =>
    ['FAILED', 'TIMEOUT'].includes(execution.status),
  ).length;
  const onlineWorkers = workers.filter((worker) => worker.status !== 'offline').length;
  const queryError =
    tasksQuery.error ?? schedulesQuery.error ?? executionsQuery.error ?? workersQuery.error;

  const invalidateScheduler = () => {
    void queryClient.invalidateQueries({ queryKey: ['scheduler'] });
  };

  const saveScheduleMutation = useMutation({
    mutationFn: ({
      scheduleId,
      payload,
    }: {
      scheduleId?: string;
      payload: SchedulePayload;
    }) =>
      scheduleId ? api.updateSchedule(scheduleId, payload) : api.createSchedule(payload),
    onSuccess: (schedule) => {
      setActionError('');
      setFormError('');
      setFormOpen(false);
      setEditingId(null);
      setForm(DEFAULT_FORM);
      setActionMessage(
        editingId ? `调度计划已更新：${schedule.id}` : `调度计划已创建：${schedule.id}`,
      );
      invalidateScheduler();
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const toggleScheduleMutation = useMutation({
    mutationFn: ({ scheduleId, enabled }: { scheduleId: string; enabled: boolean }) =>
      enabled ? api.resumeSchedule(scheduleId) : api.pauseSchedule(scheduleId),
    onSuccess: (schedule) => {
      setActionError('');
      setActionMessage(schedule.enabled ? `计划已恢复：${schedule.id}` : `计划已暂停：${schedule.id}`);
      invalidateScheduler();
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: (scheduleId: string) => api.deleteSchedule(scheduleId),
    onSuccess: (_result, scheduleId) => {
      setActionError('');
      setActionMessage(`计划已删除：${scheduleId}`);
      if (editingId === scheduleId) {
        setFormOpen(false);
        setEditingId(null);
        setForm(DEFAULT_FORM);
      }
      invalidateScheduler();
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const runTaskMutation = useMutation({
    mutationFn: ({
      taskName,
      params,
      priority,
    }: {
      taskName: string;
      params: Record<string, unknown>;
      priority: number;
    }) => api.runSchedulerTask(taskName, { params, priority }),
    onSuccess: (execution) => {
      setActionError('');
      setActionMessage(`任务已进入队列：${execution.task_name}`);
      setManualTaskName('');
      setManualParams('{}');
      setTab('executions');
      setExecutionFilter('');
      setSelectedExecutionId(execution.id);
      invalidateScheduler();
    },
    onError: (error) => setActionError(errorText(error)),
  });

  const retryExecutionMutation = useMutation({
    mutationFn: api.retryExecution,
    onSuccess: (execution) => {
      setExecutionError('');
      setActionError('');
      setActionMessage(`执行已重新入队：${execution.id}`);
      setSelectedExecutionId(execution.id);
      invalidateScheduler();
    },
    onError: (error) => setExecutionError(`重试执行失败：${errorText(error)}`),
  });

  const cancelExecutionMutation = useMutation({
    mutationFn: api.cancelExecution,
    onSuccess: (execution) => {
      setExecutionError('');
      setActionError('');
      setActionMessage(`取消请求已提交：${execution.id}`);
      setSelectedExecutionId(execution.id);
      invalidateScheduler();
    },
    onError: (error) => setExecutionError(`取消执行失败：${errorText(error)}`),
  });

  const openCreateForm = () => {
    setEditingId(null);
    setForm({
      ...DEFAULT_FORM,
      taskName: tasks[0]?.name ?? '',
    });
    setFormError('');
    setFormOpen(true);
  };

  const openEditForm = (schedule: ScheduleDefinition) => {
    setEditingId(schedule.id);
    setForm(formFromSchedule(schedule));
    setFormError('');
    setFormOpen(true);
  };

  const submitSchedule = () => {
    try {
      const payload = buildSchedulePayload(form);
      setFormError('');
      saveScheduleMutation.mutate({
        scheduleId: editingId ?? undefined,
        payload,
      });
    } catch (error) {
      setFormError(errorText(error));
    }
  };

  const openManualRun = (task: TaskDefinition) => {
    setManualTaskName(task.name);
    setManualParams('{}');
    setManualPriority(String(task.priority));
  };

  const submitManualRun = () => {
    try {
      const priority = Number(manualPriority);
      if (!Number.isInteger(priority) || priority < 0 || priority > 9) {
        throw new Error('优先级必须是 0 到 9 的整数');
      }
      runTaskMutation.mutate({
        taskName: manualTaskName,
        params: parseParams(manualParams),
        priority,
      });
    } catch (error) {
      setActionError(errorText(error));
    }
  };

  const selectExecution = (execution: TaskExecution) => {
    setExecutionError('');
    setSelectedExecutionId(execution.id);
  };

  return (
    <div className="stack scheduler-page">
      <div className="page-header">
        <div>
          <h1>任务中心</h1>
          <div className="muted">
            管理任务定义、调度计划、批量执行依赖与 Worker 在线状态。执行列表每 4 秒自动刷新。
          </div>
        </div>
        <span className="badge badge-info">
          <GitBranch size={14} />
          Planner / DAG 已启用
        </span>
      </div>

      {actionMessage ? <div className="notice">{actionMessage}</div> : null}
      {actionError ? (
        <div className="scheduler-error-banner">
          <AlertTriangle size={16} />
          <span>{actionError}</span>
        </div>
      ) : null}
      {queryError ? (
        <div className="scheduler-error-banner">
          <AlertTriangle size={16} />
          <span>任务中心加载失败：{errorText(queryError)}</span>
        </div>
      ) : null}

      <div className="grid grid-4 scheduler-metrics">
        <div className="panel stat">
          <div>
            <div className="stat-label">任务定义</div>
            <div className="stat-value">{tasks.length}</div>
            <div className="muted">已注册任务模板</div>
          </div>
          <span className="stat-icon">
            <ListChecks size={18} />
          </span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">启用计划</div>
            <div className="stat-value">{enabledSchedules}</div>
            <div className="muted">共 {schedules.length} 个计划</div>
          </div>
          <span className="stat-icon info">
            <CalendarClock size={18} />
          </span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">活动执行</div>
            <div className="stat-value">{activeExecutions}</div>
            <div className="muted">失败 / 超时 {failedExecutions}</div>
          </div>
          <span className={activeExecutions ? 'stat-icon warn' : 'stat-icon'}>
            <Activity size={18} />
          </span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">Worker 在线</div>
            <div className="stat-value">{onlineWorkers}</div>
            <div className="muted">运行任务 {workers.reduce((total, worker) => total + (worker.running_tasks ?? 0), 0)}</div>
          </div>
          <span className="stat-icon">
            <ServerCog size={18} />
          </span>
        </div>
      </div>

      <div className="panel scheduler-main-panel">
        <div className="scheduler-toolbar">
          <div className="segmented segmented-scroll" role="tablist" aria-label="任务中心视图">
            <button
              className={`button${tab === 'plans' ? ' button-primary' : ''}`}
              onClick={() => setTab('plans')}
              role="tab"
              aria-selected={tab === 'plans'}
            >
              <CalendarClock size={14} />
              计划
            </button>
            <button
              className={`button${tab === 'executions' ? ' button-primary' : ''}`}
              onClick={() => setTab('executions')}
              role="tab"
              aria-selected={tab === 'executions'}
            >
              <Activity size={14} />
              执行
            </button>
            <button
              className={`button${tab === 'workers' ? ' button-primary' : ''}`}
              onClick={() => setTab('workers')}
              role="tab"
              aria-selected={tab === 'workers'}
            >
              <ServerCog size={14} />
              Worker
            </button>
          </div>
          <div className="scheduler-toolbar-actions">
            {tab === 'executions' ? (
              <select
                aria-label="按任务筛选执行记录"
                value={executionFilter}
                onChange={(event) => {
                  setExecutionFilter(event.target.value);
                  setSelectedExecutionId('');
                }}
              >
                <option value="">全部任务</option>
                {tasks.map((task) => (
                  <option key={task.name} value={task.name}>
                    {task.name}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              className="button"
              onClick={() => {
                if (tab === 'plans') void schedulesQuery.refetch();
                if (tab === 'executions') void executionsQuery.refetch();
                if (tab === 'workers') void workersQuery.refetch();
                void tasksQuery.refetch();
              }}
              disabled={
                schedulesQuery.isFetching ||
                executionsQuery.isFetching ||
                workersQuery.isFetching ||
                tasksQuery.isFetching
              }
            >
              <RefreshCw
                size={14}
                className={
                  schedulesQuery.isFetching ||
                  executionsQuery.isFetching ||
                  workersQuery.isFetching ||
                  tasksQuery.isFetching
                    ? 'spin'
                    : undefined
                }
              />
              刷新
            </button>
          </div>
        </div>

        {tab === 'plans' ? (
          <div className="scheduler-tab-content">
            <div className="scheduler-section-head">
              <div>
                <div className="scheduler-section-title">调度计划</div>
                <div className="muted">Cron、固定间隔和单次执行统一管理。</div>
              </div>
              <button className="button button-primary" onClick={openCreateForm}>
                <Plus size={14} />
                新建计划
              </button>
            </div>

            {formOpen ? (
              <div className="scheduler-form-region">
                <div className="scheduler-form-head">
                  <div>
                    <div className="scheduler-section-title">
                      {editingId ? '编辑调度计划' : '新建调度计划'}
                    </div>
                    <div className="muted">
                      {editingId ? `计划 ID：${editingId}` : '计划 ID 创建后作为稳定标识。'}
                    </div>
                  </div>
                  <button
                    className="button scheduler-icon-button"
                    onClick={() => {
                      setFormOpen(false);
                      setEditingId(null);
                      setFormError('');
                    }}
                    aria-label="关闭计划表单"
                    title="关闭"
                  >
                    <X size={15} />
                  </button>
                </div>
                <div className="scheduler-form-grid">
                  <label className="field">
                    <span>计划 ID</span>
                    <input
                      value={form.id}
                      disabled={Boolean(editingId)}
                      onChange={(event) => setForm({ ...form, id: event.target.value })}
                      placeholder="market-sync-1m"
                    />
                  </label>
                  <label className="field">
                    <span>任务</span>
                    <select
                      value={form.taskName}
                      onChange={(event) => setForm({ ...form, taskName: event.target.value })}
                    >
                      <option value="">请选择任务</option>
                      {tasks.map((task) => (
                        <option key={task.name} value={task.name}>
                          {task.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>触发器</span>
                    <select
                      value={form.triggerType}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          triggerType: event.target.value as TriggerType,
                        })
                      }
                    >
                      <option value="cron">Cron</option>
                      <option value="interval">Interval</option>
                      <option value="fixed_delay">Fixed Delay</option>
                      <option value="date">Date</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>时区</span>
                    <input
                      value={form.timezone}
                      onChange={(event) => setForm({ ...form, timezone: event.target.value })}
                      placeholder="Asia/Shanghai"
                    />
                  </label>
                  <label className="field">
                    <span>错过策略</span>
                    <select
                      value={form.misfirePolicy}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          misfirePolicy: event.target.value as SchedulerMisfirePolicy,
                        })
                      }
                    >
                      <option value="FIRE_ONCE">FIRE_ONCE</option>
                      <option value="SKIP">SKIP</option>
                      <option value="CATCH_UP">CATCH_UP</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>最大补跑次数</span>
                    <input
                      type="number"
                      min="0"
                      value={form.maxCatchUpRuns}
                      onChange={(event) =>
                        setForm({ ...form, maxCatchUpRuns: event.target.value })
                      }
                    />
                  </label>
                </div>

                {form.triggerType === 'cron' ? (
                  <div className="scheduler-trigger-grid">
                    <label className="field">
                      <span>minute</span>
                      <input
                        value={form.minute}
                        onChange={(event) => setForm({ ...form, minute: event.target.value })}
                        placeholder="30"
                      />
                    </label>
                    <label className="field">
                      <span>hour</span>
                      <input
                        value={form.hour}
                        onChange={(event) => setForm({ ...form, hour: event.target.value })}
                        placeholder="9"
                      />
                    </label>
                    <label className="field">
                      <span>day</span>
                      <input
                        value={form.day}
                        onChange={(event) => setForm({ ...form, day: event.target.value })}
                        placeholder="*"
                      />
                    </label>
                    <label className="field">
                      <span>month</span>
                      <input
                        value={form.month}
                        onChange={(event) => setForm({ ...form, month: event.target.value })}
                        placeholder="*"
                      />
                    </label>
                    <label className="field">
                      <span>day_of_week</span>
                      <input
                        value={form.dayOfWeek}
                        onChange={(event) => setForm({ ...form, dayOfWeek: event.target.value })}
                        placeholder="1-5"
                      />
                    </label>
                  </div>
                ) : null}

                {form.triggerType === 'interval' ? (
                  <div className="scheduler-trigger-grid two-column">
                    <label className="field">
                      <span>间隔秒数</span>
                      <input
                        type="number"
                        min="1"
                        value={form.intervalSeconds}
                        onChange={(event) =>
                          setForm({ ...form, intervalSeconds: event.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      <span>开始时间（可选）</span>
                      <input
                        type="datetime-local"
                        value={form.startAt}
                        onChange={(event) => setForm({ ...form, startAt: event.target.value })}
                      />
                    </label>
                  </div>
                ) : null}

                {form.triggerType === 'fixed_delay' ? (
                  <div className="scheduler-trigger-grid two-column">
                    <label className="field">
                      <span>延迟秒数</span>
                      <input
                        type="number"
                        min="1"
                        value={form.delaySeconds}
                        onChange={(event) =>
                          setForm({ ...form, delaySeconds: event.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      <span>开始时间（可选）</span>
                      <input
                        type="datetime-local"
                        value={form.startAt}
                        onChange={(event) => setForm({ ...form, startAt: event.target.value })}
                      />
                    </label>
                  </div>
                ) : null}

                {form.triggerType === 'date' ? (
                  <div className="scheduler-trigger-grid two-column">
                    <label className="field">
                      <span>执行时间</span>
                      <input
                        type="datetime-local"
                        value={form.runAt}
                        onChange={(event) => setForm({ ...form, runAt: event.target.value })}
                      />
                    </label>
                  </div>
                ) : null}

                <label className="field scheduler-json-field">
                  <span>params JSON</span>
                  <textarea
                    className="scheduler-json-input"
                    value={form.params}
                    onChange={(event) => setForm({ ...form, params: event.target.value })}
                    spellCheck={false}
                  />
                </label>

                <div className="scheduler-form-actions">
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={form.enabled}
                      onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                    />
                    创建后启用
                  </label>
                  {formError ? <span className="error-text">{formError}</span> : null}
                  <div className="row scheduler-action-group">
                    <button
                      className="button"
                      onClick={() => {
                        setFormOpen(false);
                        setEditingId(null);
                        setFormError('');
                      }}
                      disabled={saveScheduleMutation.isPending}
                    >
                      取消
                    </button>
                    <button
                      className="button button-primary"
                      onClick={submitSchedule}
                      disabled={saveScheduleMutation.isPending || !tasks.length}
                    >
                      {saveScheduleMutation.isPending ? (
                        <Loader2 size={14} className="spin" />
                      ) : (
                        <CalendarClock size={14} />
                      )}
                      {editingId ? '保存计划' : '创建计划'}
                    </button>
                  </div>
                </div>
              </div>
            ) : null}

            {schedulesQuery.isLoading ? (
              <div className="empty">正在加载调度计划...</div>
            ) : schedules.length ? (
              <div className="table-wrap">
                <table className="table scheduler-table">
                  <thead>
                    <tr>
                      <th>计划 ID</th>
                      <th>任务</th>
                      <th>触发器</th>
                      <th>状态</th>
                      <th>下次执行</th>
                      <th>最近执行</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {schedules.map((schedule) => (
                      <tr key={schedule.id}>
                        <td>
                          <div className="scheduler-strong">{schedule.id}</div>
                          <div className="summary-subtext">{schedule.timezone}</div>
                        </td>
                        <td>{schedule.task_name}</td>
                        <td>{triggerSummary(schedule.trigger)}</td>
                        <td>
                          <span
                            className={`badge ${
                              schedule.enabled ? 'badge-ok' : 'badge-neutral'
                            }`}
                          >
                            {schedule.enabled ? '已启用' : '已暂停'}
                          </span>
                        </td>
                        <td>{formatDateTime(schedule.next_fire_at)}</td>
                        <td>{formatDateTime(schedule.last_fire_at)}</td>
                        <td>
                          <div className="scheduler-action-group">
                            <button
                              className="button scheduler-icon-button"
                              onClick={() => openEditForm(schedule)}
                              title="编辑计划"
                              aria-label={`编辑计划 ${schedule.id}`}
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              className="button scheduler-icon-button"
                              onClick={() =>
                                toggleScheduleMutation.mutate({
                                  scheduleId: schedule.id,
                                  enabled: !schedule.enabled,
                                })
                              }
                              disabled={toggleScheduleMutation.isPending}
                              title={schedule.enabled ? '暂停计划' : '恢复计划'}
                              aria-label={`${schedule.enabled ? '暂停' : '恢复'}计划 ${schedule.id}`}
                            >
                              {schedule.enabled ? <Pause size={14} /> : <Play size={14} />}
                            </button>
                            <button
                              className="button scheduler-icon-button"
                              onClick={() => {
                                setTab('executions');
                                setExecutionFilter(schedule.task_name);
                                setSelectedExecutionId('');
                              }}
                              title="查看执行历史"
                              aria-label={`查看计划 ${schedule.id} 的执行历史`}
                            >
                              <History size={14} />
                            </button>
                            <button
                              className="button button-danger scheduler-icon-button"
                              onClick={() => {
                                if (window.confirm(`确认删除调度计划 ${schedule.id}？`)) {
                                  deleteScheduleMutation.mutate(schedule.id);
                                }
                              }}
                              disabled={deleteScheduleMutation.isPending}
                              title="删除计划"
                              aria-label={`删除计划 ${schedule.id}`}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">暂无调度计划。新建计划后即可按时间自动触发任务。</div>
            )}

            <div className="scheduler-section-head scheduler-task-head">
              <div>
                <div className="scheduler-section-title">任务目录</div>
                <div className="muted">手动运行同样会创建 Execution 并进入 Dispatcher 队列。</div>
              </div>
            </div>

            {manualTaskName ? (
              <div className="scheduler-run-region">
                <div className="scheduler-form-head">
                  <div>
                    <div className="scheduler-section-title">立即运行 {manualTaskName}</div>
                    <div className="muted">提交前会校验 params JSON 与优先级。</div>
                  </div>
                  <button
                    className="button scheduler-icon-button"
                    onClick={() => setManualTaskName('')}
                    title="关闭"
                    aria-label="关闭手动运行面板"
                  >
                    <X size={15} />
                  </button>
                </div>
                <div className="scheduler-run-grid">
                  <label className="field">
                    <span>优先级</span>
                    <input
                      type="number"
                      min="0"
                      max="9"
                      value={manualPriority}
                      onChange={(event) => setManualPriority(event.target.value)}
                    />
                  </label>
                  <label className="field scheduler-run-json">
                    <span>params JSON</span>
                    <textarea
                      className="scheduler-json-input compact"
                      value={manualParams}
                      onChange={(event) => setManualParams(event.target.value)}
                      spellCheck={false}
                    />
                  </label>
                </div>
                <div className="row scheduler-form-actions">
                  <span className="muted">手动任务会记录 trace、状态、耗时和失败原因。</span>
                  <button
                    className="button button-primary"
                    onClick={submitManualRun}
                    disabled={runTaskMutation.isPending}
                  >
                    {runTaskMutation.isPending ? (
                      <Loader2 size={14} className="spin" />
                    ) : (
                      <Play size={14} />
                    )}
                    确认运行
                  </button>
                </div>
              </div>
            ) : null}

            {tasksQuery.isLoading ? (
              <div className="empty">正在加载任务定义...</div>
            ) : tasks.length ? (
              <div className="table-wrap">
                <table className="table scheduler-table scheduler-task-table">
                  <thead>
                    <tr>
                      <th>任务</th>
                      <th>Queue</th>
                      <th>超时</th>
                      <th>并发策略</th>
                      <th>优先级</th>
                      <th>重试</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map((task) => (
                      <tr key={task.name}>
                        <td>
                          <div className="scheduler-strong">{task.name}</div>
                          <div className="summary-subtext">{task.description || task.handler}</div>
                          {task.planner ? (
                            <div className="summary-subtext">Planner · {task.planner}</div>
                          ) : null}
                        </td>
                        <td>
                          <span className="tag">{task.queue}</span>
                        </td>
                        <td>{task.timeout_seconds}s</td>
                        <td>{task.concurrency_policy}</td>
                        <td>{task.priority}</td>
                        <td>{task.retry_policy?.max_attempts ?? 1}</td>
                        <td>
                          <span
                            className={`badge ${task.enabled ? 'badge-ok' : 'badge-neutral'}`}
                          >
                            {task.enabled ? '可用' : '停用'}
                          </span>
                        </td>
                        <td>
                          <button
                            className="button"
                            onClick={() => openManualRun(task)}
                            disabled={!task.enabled}
                          >
                            <Play size={14} />
                            立即运行
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">暂无已注册任务。</div>
            )}
          </div>
        ) : null}

        {tab === 'executions' ? (
          <div className="scheduler-tab-content">
            <div className="scheduler-section-head">
              <div>
                <div className="scheduler-section-title">执行记录</div>
                <div className="muted">
                  自动刷新中 · 当前显示 {executions.length} 条记录
                  {executionFilter ? ` · ${executionFilter}` : ''}
                </div>
              </div>
              <span className="badge badge-info">
                <Clock3 size={14} />
                4s
              </span>
            </div>

            {executionError ? (
              <div className="scheduler-error-banner">
                <AlertTriangle size={16} />
                <span>{executionError}</span>
              </div>
            ) : null}

            {executionsQuery.isLoading ? (
              <div className="empty">正在加载执行记录...</div>
            ) : executions.length ? (
              <div className="table-wrap">
                <table className="table scheduler-table execution-table">
                  <thead>
                    <tr>
                      <th>状态</th>
                      <th>任务</th>
                      <th>开始时间</th>
                      <th>耗时</th>
                      <th>Worker</th>
                      <th>依赖</th>
                      <th>尝试</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executions.map((execution) => {
                      const selected = execution.id === selectedExecutionId;
                      const canRetry = RETRYABLE_STATUSES.has(execution.status);
                      const canCancel = ACTIVE_STATUSES.has(execution.status);
                      return (
                        <tr
                          key={execution.id}
                          className={`scheduler-clickable-row${selected ? ' selected' : ''}`}
                          onClick={() => selectExecution(execution)}
                        >
                          <td>
                            <span className={`badge scheduler-status-${statusTone(execution.status)}`}>
                              {statusLabel(execution.status)}
                            </span>
                          </td>
                          <td>
                            <div className="scheduler-strong">{execution.task_name}</div>
                            <div className="summary-subtext">{shortId(execution.id, 12)}</div>
                            {execution.error_message ? (
                              <div className="error-text scheduler-row-error">
                                {execution.error_message}
                              </div>
                            ) : null}
                          </td>
                          <td>{formatDateTime(execution.started_at ?? execution.scheduled_at)}</td>
                          <td>{durationLabel(execution.duration_ms)}</td>
                          <td>{execution.worker_id ? shortId(execution.worker_id, 14) : '--'}</td>
                          <td>
                            {(execution.depends_on ?? []).length ? (
                              <span className="tag">
                                {(execution.depends_on ?? []).length} 个依赖
                              </span>
                            ) : execution.parent_execution_id ? (
                              <span className="tag">父任务</span>
                            ) : (
                              '--'
                            )}
                          </td>
                          <td>
                            {execution.attempt}/{execution.max_attempts}
                          </td>
                          <td>
                            <div
                              className="scheduler-action-group"
                              onClick={(event) => event.stopPropagation()}
                            >
                              <button
                                className="button scheduler-icon-button"
                                onClick={() => retryExecutionMutation.mutate(execution.id)}
                                disabled={!canRetry || retryExecutionMutation.isPending}
                                title="重试执行"
                                aria-label={`重试执行 ${execution.id}`}
                              >
                                <RotateCcw size={14} />
                              </button>
                              <button
                                className="button button-danger scheduler-icon-button"
                                onClick={() => cancelExecutionMutation.mutate(execution.id)}
                                disabled={!canCancel || cancelExecutionMutation.isPending}
                                title="取消执行"
                                aria-label={`取消执行 ${execution.id}`}
                              >
                                <Square size={13} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">当前筛选条件下暂无执行记录。</div>
            )}

            <div className="scheduler-dag-panel">
              <div className="scheduler-section-head">
                <div>
                  <div className="scheduler-section-title">依赖 DAG</div>
                  <div className="muted">
                    {selectedExecution
                      ? `${selectedExecution.task_name} · ${selectedExecution.id}`
                      : '选择一条执行记录查看依赖节点和边。'}
                  </div>
                </div>
                {selectedExecution ? (
                  <span className={`badge scheduler-status-${statusTone(selectedExecution.status)}`}>
                    {statusLabel(selectedExecution.status)}
                  </span>
                ) : null}
              </div>
              {executionDetailQuery.isLoading && selectedExecutionId ? (
                <div className="empty">正在加载执行详情...</div>
              ) : executionDetailQuery.isError ? (
                <div className="scheduler-error-banner">
                  <AlertTriangle size={16} />
                  <span>执行详情加载失败：{errorText(executionDetailQuery.error)}</span>
                </div>
              ) : selectedExecution ? (
                <DependencyGraph selected={selectedExecution} executions={executions} />
              ) : (
                <div className="empty">选择执行记录后展示父任务、当前任务与下游任务。</div>
              )}
            </div>
          </div>
        ) : null}

        {tab === 'workers' ? (
          <div className="scheduler-tab-content">
            <div className="scheduler-section-head">
              <div>
                <div className="scheduler-section-title">Worker 状态</div>
                <div className="muted">心跳信息用于判断 Worker 是否在线及当前负载。</div>
              </div>
              <span className="badge badge-ok">
                <ServerCog size={14} />
                {workers.length} 个注册节点
              </span>
            </div>

            {workersQuery.isLoading ? (
              <div className="empty">正在加载 Worker...</div>
            ) : workers.length ? (
              <div className="table-wrap">
                <table className="table scheduler-table worker-table">
                  <thead>
                    <tr>
                      <th>Worker</th>
                      <th>主机</th>
                      <th>PID</th>
                      <th>队列</th>
                      <th>状态</th>
                      <th>运行任务</th>
                      <th>最近心跳</th>
                      <th>启动时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workers.map((worker: WorkerSummary) => (
                      <tr key={worker.worker_id}>
                        <td className="scheduler-strong">{worker.worker_id}</td>
                        <td>{worker.hostname || '--'}</td>
                        <td>{worker.pid ?? '--'}</td>
                        <td>
                          {worker.queues?.length
                            ? worker.queues.map((queue) => (
                                <span key={queue} className="tag scheduler-queue-tag">
                                  {queue}
                                </span>
                              ))
                            : '--'}
                        </td>
                        <td>
                          <span
                            className={`badge ${
                              worker.status === 'offline' ? 'badge-neutral' : 'badge-ok'
                            }`}
                          >
                            {worker.status || 'online'}
                          </span>
                        </td>
                        <td>{worker.running_tasks ?? 0}</td>
                        <td>{formatDateTime(worker.last_heartbeat)}</td>
                        <td>{formatDateTime(worker.started_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">暂无 Worker 心跳。Redis 模式下启动 Worker 后会显示。</div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Ban,
  BrainCircuit,
  CheckCircle2,
  Gauge,
  Plus,
  Rocket,
  ScrollText,
  TrendingUp,
} from 'lucide-react';
import { trainingApi } from '../../api/alphalab/training';
import { api } from '../../api/client';
import {
  AlphaLabEmpty,
  AlphaLabError,
  AlphaLabLoading,
  AlphaLabMetric,
  AlphaLabQueryStatus,
  AlphaLabStatus,
} from '../../components/alphalab/AlphaLabStates';
import {
  AlphaLabHelp,
  AlphaLabLabel,
  AlphaLabNote,
} from '../../components/alphalab/AlphaLabHelp';
import {
  errorText,
  formatCompact,
  formatDateTime,
  formatNumber,
  formatPercent,
  progressValue,
  shortId,
} from '../../components/alphalab/format';
import type { DatasetSummary } from '../../types';
import type {
  TrainingLogEntry,
  TrainingMetricsPoint,
  TrainingRun,
} from '../../types/alphalab/training';
import {
  filterDailyDatasets,
  formatTimeframeLabel,
  isDailyDataset,
} from '../../utils/datasetDisplay';
import '../../styles/alphalab.css';

const ACTIVE_STATUSES = new Set(['PENDING', 'QUEUED', 'RUNNING']);

// 训练所需最少 bar 数。仅用于前端提示，与后端 training_min_bars 对齐；
// 最终校验仍以后端为准（不足时会返回 insufficient training bars 错误）。
const TRAINING_MIN_BARS = 300;

function hasEnoughBars(dataset: DatasetSummary): boolean {
  return dataset.bar_count >= TRAINING_MIN_BARS;
}

// 曲线最多绘制的点数；超出时按步长抽样，保留首尾点。
const MAX_CURVE_POINTS = 400;

const CURVE_COLORS = {
  reward: '#17746c',
  validation: '#345b8c',
  best: '#a15c07',
  ic: '#17746c',
  rankIc: '#345b8c',
  entropy: '#a15c07',
} as const;

interface TrainingFormState {
  name: string;
  datasetId: string;
  configProfile: string;
  seed: string;
  totalSteps: string;
  batchSize: string;
  device: 'auto' | 'cpu' | 'cuda' | 'mps';
  fromScratch: boolean;
}

const DEFAULT_FORM: TrainingFormState = {
  name: '',
  datasetId: '',
  configProfile: 'alpha_master_compat',
  seed: '42',
  totalSteps: '1000',
  batchSize: '256',
  device: 'auto',
  fromScratch: true,
};

interface TrainingCurvePoint {
  step: number;
  reward: number | null;
  validation_score: number | null;
  best_score: number | null;
  ic: number | null;
  rank_ic: number | null;
  entropy: number | null;
}

function datasetOptionLabel(dataset: DatasetSummary): string {
  const title = dataset.title?.trim();
  const name = title || dataset.symbol;
  const symbol = title && title !== dataset.symbol ? ` · ${dataset.symbol}` : '';
  const warning = hasEnoughBars(dataset)
    ? ''
    : `（数据不足，仅 ${dataset.bar_count} 根，至少需 ${TRAINING_MIN_BARS} 根）`;
  return `${name}${symbol} · ${formatTimeframeLabel(dataset.timeframe)} · ${dataset.bar_count} 根${warning}`;
}

function isActive(run: TrainingRun): boolean {
  return ACTIVE_STATUSES.has(String(run.status).toUpperCase());
}

function runProgress(run: TrainingRun): number {
  if (run.progress !== undefined) {
    return run.progress <= 1 ? run.progress * 100 : run.progress;
  }
  return progressValue(run.current_step, run.total_steps);
}

function runFormula(run?: TrainingRun): string {
  if (!run) return '--';
  const candidate = run.candidates?.find(
    (item) => item.strategy_id && item.strategy_id === run.best_strategy_id,
  );
  return (
    run.best_formula ||
    candidate?.formula_expression ||
    candidate?.formula ||
    '--'
  );
}

/** 只保留真实存在的数值，缺失一律按 -- 展示，不补默认值。 */
function finiteOrNull(value: number | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 训练配置（config_json）里的字段，缺失时返回 --。 */
function configText(run: TrainingRun, key: string): string {
  return formatCompact(run.config_json?.[key]);
}

/** 历史点数过多时抽样：保留第一个、最后一个，中间等距取点。 */
function downsampleHistory(
  history: TrainingMetricsPoint[],
): TrainingMetricsPoint[] {
  if (history.length <= MAX_CURVE_POINTS) return history;
  const lastIndex = history.length - 1;
  const stride = lastIndex / (MAX_CURVE_POINTS - 1);
  const sampled: TrainingMetricsPoint[] = [];
  let previousIndex = -1;
  for (let index = 0; index < MAX_CURVE_POINTS; index += 1) {
    const sourceIndex = Math.round(index * stride);
    if (sourceIndex === previousIndex) continue;
    sampled.push(history[sourceIndex]);
    previousIndex = sourceIndex;
  }
  return sampled;
}

function buildCurve(history: TrainingMetricsPoint[]): TrainingCurvePoint[] {
  return downsampleHistory(history).map((point) => ({
    step: point.step,
    reward: finiteOrNull(point.reward),
    validation_score: finiteOrNull(point.validation_score),
    best_score: finiteOrNull(point.best_score),
    ic: finiteOrNull(point.ic),
    rank_ic: finiteOrNull(point.rank_ic),
    entropy: finiteOrNull(point.entropy),
  }));
}

function logLevel(level: string): 'info' | 'warn' | 'error' {
  const value = String(level).toLowerCase();
  if (value === 'warn' || value === 'warning') return 'warn';
  if (value === 'error' || value === 'fatal' || value === 'critical') {
    return 'error';
  }
  return 'info';
}

function hasValue(
  points: TrainingCurvePoint[],
  key: keyof Omit<TrainingCurvePoint, 'step'>,
): boolean {
  return points.some((point) => point[key] !== null);
}

/**
 * 任务详情里的指标块。AlphaLabMetric 的 label 只接受字符串，
 * 而这里需要「中文主标题 + 英文次级」的 AlphaLabLabel，
 * 因此沿用同一套结构类名（.alphalab-metric / .stat-label / .alphalab-metric-value）。
 */
function DetailMetric({
  zh,
  en,
  value,
  detail,
}: {
  zh: string;
  en: string;
  value: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="alphalab-metric">
      <div className="stat-label">
        <AlphaLabLabel zh={zh} en={en} />
      </div>
      <div className="alphalab-metric-value">{value}</div>
      {detail ? <div className="alphalab-metric-detail">{detail}</div> : null}
    </div>
  );
}

export function TrainingPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TrainingFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const logViewRef = useRef<HTMLDivElement | null>(null);

  const overviewQuery = useQuery({
    queryKey: ['alphalab', 'training', 'overview'],
    queryFn: trainingApi.overview,
    refetchInterval: 15_000,
  });
  const runsQuery = useQuery({
    queryKey: ['alphalab', 'training', 'runs'],
    queryFn: trainingApi.list,
    refetchInterval: 5_000,
  });
  const datasetsQuery = useQuery({
    queryKey: ['datasets'],
    queryFn: api.listDatasets,
  });
  const detailQuery = useQuery({
    queryKey: ['alphalab', 'training', 'run', selectedId],
    queryFn: () => trainingApi.get(selectedId),
    enabled: Boolean(selectedId),
    refetchInterval: selectedId ? 4_000 : false,
  });

  const runs = runsQuery.data ?? [];
  const dailyDatasets = filterDailyDatasets(datasetsQuery.data?.items ?? []);

  useEffect(() => {
    if (!dailyDatasets.length || form.datasetId) return;
    const dailyDataset =
      dailyDatasets.find(isDailyDataset) ?? dailyDatasets[0];
    setForm((current) => {
      if (current.datasetId) return current;
      return {
        ...current,
        datasetId: dailyDataset.id,
        name: dailyDataset.title?.trim() || dailyDataset.symbol.trim(),
      };
    });
  }, [dailyDatasets, form.datasetId]);
  const selectedDataset = dailyDatasets.find(
    (dataset) => dataset.id === form.datasetId,
  );
  const selectedRun =
    detailQuery.data ?? runs.find((run) => run.id === selectedId) ?? null;
  const activeRuns = runs.filter(isActive).length;
  const completedRuns = runs.filter((run) =>
    ['SUCCEEDED', 'SUCCESS'].includes(String(run.status).toUpperCase()),
  ).length;
  const failedRuns = runs.filter((run) =>
    ['FAILED', 'CANCELLED', 'STOPPED'].includes(String(run.status).toUpperCase()),
  ).length;
  const queryError = overviewQuery.error ?? runsQuery.error ?? detailQuery.error;
  const updatedAt = Math.max(
    overviewQuery.dataUpdatedAt,
    runsQuery.dataUpdatedAt,
    datasetsQuery.dataUpdatedAt,
    detailQuery.dataUpdatedAt,
  );

  const metricsHistory = selectedRun?.metrics_history;
  const curve = useMemo(() => buildCurve(metricsHistory ?? []), [metricsHistory]);
  const hasRewardCurve = hasValue(curve, 'reward');
  const hasValidationCurve = hasValue(curve, 'validation_score');
  const hasBestCurve = hasValue(curve, 'best_score');
  const hasIcCurve = hasValue(curve, 'ic');
  const hasRankIcCurve = hasValue(curve, 'rank_ic');
  const hasEntropyCurve = hasValue(curve, 'entropy');
  const hasScoreChart = hasRewardCurve || hasValidationCurve || hasBestCurve;
  const hasCoefficientChart = hasIcCurve || hasRankIcCurve || hasEntropyCurve;

  const logs: TrainingLogEntry[] = selectedRun?.logs ?? [];
  const logCount = logs.length;
  const selectedIsActive = selectedRun ? isActive(selectedRun) : false;

  // 运行中的任务：每次出现新日志行就滚动到最新一行。
  useEffect(() => {
    if (!selectedIsActive) return;
    const node = logViewRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [selectedId, selectedIsActive, logCount]);

  const createMutation = useMutation({
    mutationFn: trainingApi.create,
    onSuccess: async (run) => {
      setForm(DEFAULT_FORM);
      setFormError('');
      setActionMessage(`训练任务已创建：${run.id}`);
      setSelectedId(run.id);
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'training'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const cancelMutation = useMutation({
    mutationFn: trainingApi.cancel,
    onSuccess: async (run) => {
      setActionMessage(`取消请求已提交：${run.id}`);
      setSelectedId(run.id);
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'training'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const refresh = () => {
    void overviewQuery.refetch();
    void runsQuery.refetch();
    void datasetsQuery.refetch();
    if (selectedId) void detailQuery.refetch();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError('');
    setActionMessage('');
    if (!dailyDatasets.length) {
      setFormError('暂无可用于训练的数据集，请先在数据中心导入行情');
      return;
    }
    if (!selectedDataset) {
      setFormError('请选择一个已有数据集');
      return;
    }
    if (!selectedDataset.symbol.trim()) {
      setFormError('所选数据集缺少标的');
      return;
    }
    if (!selectedDataset.timeframe.trim()) {
      setFormError('所选数据集缺少周期');
      return;
    }
    if (!hasEnoughBars(selectedDataset)) {
      setFormError(
        `数据集仅 ${selectedDataset.bar_count} 根 bar，训练至少需要 ${TRAINING_MIN_BARS} 根，请选择更长历史的数据集`,
      );
      return;
    }
    const seed = Number(form.seed);
    const totalSteps = Number(form.totalSteps);
    const batchSize = Number(form.batchSize);
    if (!Number.isInteger(seed) || seed < 0) {
      setFormError('seed 必须是非负整数');
      return;
    }
    if (!Number.isInteger(totalSteps) || totalSteps <= 0) {
      setFormError('steps 必须是正整数');
      return;
    }
    if (!Number.isInteger(batchSize) || batchSize <= 0) {
      setFormError('batch 必须是正整数');
      return;
    }
    createMutation.mutate({
      name: form.name.trim() || undefined,
      symbols: [selectedDataset.symbol.trim()],
      timeframe: selectedDataset.timeframe.trim(),
      data_snapshot_id: selectedDataset.id,
      config_profile: form.configProfile.trim() || undefined,
      seed,
      from_scratch: form.fromScratch,
      total_steps: totalSteps,
      batch_size: batchSize,
      device: form.device,
    });
  };

  return (
    <div className="stack alphalab-page">
      <div className="page-header">
        <div>
          <h1>因子训练</h1>
          <div className="row">
            <AlphaLabStatus status={selectedRun?.status} />
            <span className="tag">{runs.length} 个训练任务</span>
          </div>
        </div>
        <AlphaLabQueryStatus
          isFetching={
            overviewQuery.isFetching ||
            runsQuery.isFetching ||
            datasetsQuery.isFetching ||
            detailQuery.isFetching
          }
          isError={Boolean(queryError || datasetsQuery.error)}
          updatedAt={updatedAt}
          onRefresh={refresh}
        />
      </div>

      {actionMessage ? <div className="notice">{actionMessage}</div> : null}
      {queryError ? <AlphaLabError error={queryError} onRetry={refresh} /> : null}

      <div className="alphalab-metric-grid">
        <div className="panel">
          <AlphaLabMetric
            label="运行中训练"
            value={
              activeRuns ||
              overviewQuery.data?.training?.running ||
              overviewQuery.data?.training_running ||
              overviewQuery.data?.running_training ||
              0
            }
            detail="实时状态"
          />
        </div>
        <div className="panel">
          <AlphaLabMetric label="已完成" value={completedRuns} detail="当前列表" />
        </div>
        <div className="panel">
          <AlphaLabMetric label="失败 / 取消" value={failedRuns} detail="当前列表" />
        </div>
        <div className="panel">
          <AlphaLabMetric
            label="资源"
            value={formatCompact(overviewQuery.data?.resource_status)}
            detail="训练执行器"
          />
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Plus size={15} />
            新建训练
          </span>
        </div>
        <form onSubmit={submit}>
          <div className="form-grid alphalab-form-grid">
            <div className="field" style={{ gridColumn: '1 / -1' }}>
              <label htmlFor="alpha-training-dataset">数据集</label>
              <select
                id="alpha-training-dataset"
                value={form.datasetId}
                onChange={(event) => {
                  const dataset = dailyDatasets.find(
                    (item) => item.id === event.target.value,
                  );
                  setForm((current) => ({
                    ...current,
                    datasetId: event.target.value,
                    name: dataset
                      ? dataset.title?.trim() || dataset.symbol.trim()
                      : '',
                  }));
                }}
                disabled={
                  datasetsQuery.isLoading ||
                  datasetsQuery.isError ||
                  dailyDatasets.length === 0
                }
              >
                <option value="">
                  {datasetsQuery.isLoading
                    ? '正在加载数据集...'
                    : datasetsQuery.isError
                      ? '数据集加载失败'
                      : dailyDatasets.length
                        ? '请选择数据集'
                        : '暂无可用数据集'}
                </option>
                {dailyDatasets.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {datasetOptionLabel(dataset)}
                  </option>
                ))}
              </select>
              {datasetsQuery.isLoading ? (
                <div className="muted">正在加载可用数据集...</div>
              ) : null}
              {datasetsQuery.isError ? (
                <div className="row">
                  <span className="error-text">
                    数据集加载失败：{errorText(datasetsQuery.error)}
                  </span>
                  <button
                    type="button"
                    className="button"
                    onClick={() => void datasetsQuery.refetch()}
                  >
                    重试
                  </button>
                </div>
              ) : null}
              {!datasetsQuery.isLoading &&
              !datasetsQuery.isError &&
              dailyDatasets.length === 0 ? (
                <div className="error-text">
                  暂无可用于训练的数据集，请先在数据中心导入行情。
                </div>
              ) : null}
            </div>
            <div className="field">
              <label htmlFor="alpha-training-name">任务名称</label>
              <input
                id="alpha-training-name"
                value={form.name}
                onChange={(event) =>
                  setForm((current) => ({ ...current, name: event.target.value }))
                }
                placeholder="可选"
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-symbols">标的</label>
              <input
                id="alpha-training-symbols"
                value={selectedDataset?.symbol ?? ''}
                placeholder="选择数据集后自动填充"
                readOnly
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-timeframe">周期</label>
              <input
                id="alpha-training-timeframe"
                value={selectedDataset?.timeframe ?? ''}
                placeholder="选择数据集后自动填充"
                readOnly
              />
              <div className="muted">
                训练默认使用日周期（1d）数据集，分钟周期仅用于盘中多周期分析。
              </div>
            </div>
            <div className="field">
              <label htmlFor="alpha-training-snapshot">数据快照</label>
              <input
                id="alpha-training-snapshot"
                value={selectedDataset?.id ?? ''}
                placeholder="选择数据集后自动填充"
                readOnly
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-profile">配置 Profile</label>
              <input
                id="alpha-training-profile"
                value={form.configProfile}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    configProfile: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-seed">Seed</label>
              <input
                id="alpha-training-seed"
                type="number"
                min="0"
                value={form.seed}
                onChange={(event) =>
                  setForm((current) => ({ ...current, seed: event.target.value }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-steps">Steps</label>
              <input
                id="alpha-training-steps"
                type="number"
                min="1"
                value={form.totalSteps}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    totalSteps: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-batch">Batch</label>
              <input
                id="alpha-training-batch"
                type="number"
                min="1"
                value={form.batchSize}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    batchSize: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-training-device">Device</label>
              <select
                id="alpha-training-device"
                value={form.device}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    device: event.target.value as TrainingFormState['device'],
                  }))
                }
              >
                <option value="auto">auto</option>
                <option value="cpu">cpu</option>
                <option value="cuda">cuda</option>
                <option value="mps">mps</option>
              </select>
            </div>
            <label className="checkbox-row alphalab-checkbox">
              <input
                type="checkbox"
                checked={form.fromScratch}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    fromScratch: event.target.checked,
                  }))
                }
              />
              从头训练
            </label>
          </div>
          {formError ? <div className="error-text">{formError}</div> : null}
          <div className="row alphalab-form-actions">
            <button
              className="button button-primary"
              type="submit"
              disabled={createMutation.isPending || !selectedDataset}
            >
              <Rocket size={14} />
              {createMutation.isPending ? '提交中' : '开始训练'}
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <BrainCircuit size={15} />
            训练任务
          </span>
          <span className="tag">{runs.length} 条</span>
        </div>
        {runsQuery.isLoading ? (
          <AlphaLabLoading label="加载训练任务" />
        ) : runsQuery.isError ? (
          <AlphaLabError error={runsQuery.error} onRetry={() => void runsQuery.refetch()} />
        ) : runs.length ? (
          <div className="table-wrap">
            <table className="table alphalab-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>标的</th>
                  <th>周期</th>
                  <th>状态</th>
                  <th>进度</th>
                  <th>Best</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const progress = runProgress(run);
                  return (
                    <tr
                      key={run.id}
                      className={selectedId === run.id ? 'selected-row' : undefined}
                      onClick={() => setSelectedId(run.id)}
                    >
                      <td>
                        <div className="alphalab-cell-main">{run.name || shortId(run.id)}</div>
                        <div className="code">{shortId(run.id)}</div>
                      </td>
                      <td>{run.symbols?.join(', ') || '--'}</td>
                      <td>{run.timeframe || '--'}</td>
                      <td>
                        <AlphaLabStatus status={run.status} />
                      </td>
                      <td>
                        <div className="alphalab-progress-cell">
                          <div className="progress">
                            <div className="progress-fill" style={{ width: `${progress}%` }} />
                          </div>
                          <span>
                            {run.current_step ?? 0}/{run.total_steps ?? 0}
                          </span>
                        </div>
                      </td>
                      <td>{formatNumber(run.best_score, 4)}</td>
                      <td>{formatDateTime(run.finished_at || run.started_at || run.created_at)}</td>
                      <td>
                        <button
                          type="button"
                          className="button button-danger"
                          disabled={!isActive(run) || cancelMutation.isPending}
                          onClick={(event) => {
                            event.stopPropagation();
                            cancelMutation.mutate(run.id);
                          }}
                        >
                          <Ban size={14} />
                          取消
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <AlphaLabEmpty>暂无训练任务</AlphaLabEmpty>
        )}
      </div>

      <div className="panel alphalab-detail-panel">
        <div className="section-title">
          <span className="section-title-main">
            <Gauge size={15} />
            <AlphaLabLabel zh="任务详情" en="Task Detail" />
          </span>
          {selectedRun ? <AlphaLabStatus status={selectedRun.status} /> : null}
        </div>
        {!selectedId ? (
          <AlphaLabEmpty>选择一条训练任务查看详情</AlphaLabEmpty>
        ) : detailQuery.isLoading ? (
          <AlphaLabLoading label="加载训练详情" />
        ) : detailQuery.isError && !selectedRun ? (
          <AlphaLabError
            error={detailQuery.error}
            onRetry={() => void detailQuery.refetch()}
          />
        ) : selectedRun ? (
          <div className="stack compact">
            <div className="inline-meta">
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="任务编号" en="Task ID" />
                </div>
                <div className="value code">{selectedRun.id || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="市场" en="Market" />
                </div>
                <div className="value">{selectedRun.market || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="标的" en="Symbols" />
                </div>
                <div className="value">{selectedRun.symbols?.join(', ') || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="周期" en="Timeframe" />
                </div>
                <div className="value">{selectedRun.timeframe || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="数据集" en="Dataset" />
                </div>
                <div className="value">
                  {selectedRun.dataset_title || selectedRun.dataset_id || '--'}
                </div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="因子词表版本" en="Schema" />
                </div>
                <div className="value">
                  {selectedRun.factor_schema_version || '--'}
                </div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="数据快照" en="Snapshot" />
                </div>
                <div className="value code">{selectedRun.data_snapshot_id || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="配置 Profile" en="Profile" />
                </div>
                <div className="value">
                  {selectedRun.config_profile || configText(selectedRun, 'profile')}
                </div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="种子" en="Seed" />
                </div>
                <div className="value">{formatCompact(selectedRun.seed)}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="训练步数" en="Steps" />
                </div>
                <div className="value">{formatCompact(selectedRun.total_steps)}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="批量" en="Batch" />
                </div>
                <div className="value">{configText(selectedRun, 'batch_size')}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="执行节点" en="Worker" />
                </div>
                <div className="value">{selectedRun.worker_id || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="最优得分" en="Best Score" />
                </div>
                <div className="value">
                  {formatNumber(
                    selectedRun.best_score ?? selectedRun.metrics_json?.best_score,
                  )}
                </div>
              </div>
              <div className="meta-item">
                <div className="label">
                  <AlphaLabLabel zh="检查点" en="Checkpoint" />
                </div>
                <div className="value code alphalab-compact-cell">
                  {selectedRun.checkpoint_uri || '--'}
                </div>
              </div>
            </div>

            <div className="alphalab-detail-metrics">
              <DetailMetric
                zh="总进度"
                en="Progress"
                value={`${formatNumber(runProgress(selectedRun), 1)}%`}
                detail={
                  selectedRun.total_steps
                    ? `${selectedRun.current_step ?? 0}/${selectedRun.total_steps}`
                    : `Step ${selectedRun.current_step ?? selectedRun.step ?? 0}`
                }
              />
              <DetailMetric
                zh="奖励"
                en="Reward"
                value={formatNumber(selectedRun.metrics_json?.reward)}
              />
              <DetailMetric
                zh="验证得分"
                en="Validation"
                value={formatNumber(selectedRun.metrics_json?.validation_score)}
              />
              <DetailMetric
                zh="信息系数"
                en="IC"
                value={formatNumber(selectedRun.metrics_json?.ic)}
              />
              <DetailMetric
                zh="排序信息系数"
                en="Rank IC"
                value={formatNumber(selectedRun.metrics_json?.rank_ic)}
              />
              <DetailMetric
                zh="熵"
                en="Entropy"
                value={formatNumber(selectedRun.metrics_json?.entropy)}
              />
              <DetailMetric
                zh="精英池"
                en="Elite Pool"
                value={formatNumber(selectedRun.metrics_json?.elite_pool_size, 0)}
              />
              <DetailMetric
                zh="无效比例"
                en="Invalid Rate"
                value={formatPercent(selectedRun.metrics_json?.invalid_rate)}
              />
              <DetailMetric
                zh="最优得分"
                en="Best Score"
                value={formatNumber(
                  selectedRun.best_score ?? selectedRun.metrics_json?.best_score,
                )}
              />
            </div>

            <div className="alphalab-formula">
              <div className="label">
                <AlphaLabLabel zh="最优因子公式" en="Best Formula" />
              </div>
              <div className="code">{runFormula(selectedRun)}</div>
            </div>

            {selectedRun.error_message ? (
              <div className="alphalab-inline-error">{selectedRun.error_message}</div>
            ) : null}

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <CheckCircle2 size={15} />
                  <AlphaLabLabel zh="候选策略" en="Top Candidates" />
                </span>
              </div>
              {selectedRun.candidates?.length ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>
                          <AlphaLabLabel zh="排名" en="Rank" />
                        </th>
                        <th>
                          <AlphaLabLabel zh="策略" en="Strategy" />
                        </th>
                        <th>
                          <AlphaLabLabel zh="公式" en="Formula" />
                        </th>
                        <th>
                          <AlphaLabLabel zh="训练得分" en="Train" />
                        </th>
                        <th>
                          <AlphaLabLabel zh="验证得分" en="Validation" />
                        </th>
                        <th>
                          <AlphaLabLabel zh="总分" en="Total" />
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedRun.candidates.map((candidate, index) => (
                        <tr key={candidate.strategy_id || candidate.id || index}>
                          <td>{candidate.rank ?? index + 1}</td>
                          <td className="code">
                            {shortId(candidate.strategy_id || candidate.id)}
                          </td>
                          <td className="alphalab-formula-cell">
                            {candidate.formula_expression || candidate.formula || '--'}
                          </td>
                          <td>{formatNumber(candidate.train_score)}</td>
                          <td>{formatNumber(candidate.validation_score)}</td>
                          <td>{formatNumber(candidate.score)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <AlphaLabEmpty>暂无候选策略</AlphaLabEmpty>
              )}
            </div>
          </div>
        ) : (
          <AlphaLabEmpty>暂无训练详情</AlphaLabEmpty>
        )}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <TrendingUp size={15} />
            <AlphaLabLabel zh="训练曲线" en="Training Curves" />
          </span>
          {curve.length ? (
            <span className="tag">
              {metricsHistory?.length ?? 0} 个采样点
              {curve.length < (metricsHistory?.length ?? 0)
                ? ` · 图表抽样 ${curve.length} 点`
                : ''}
            </span>
          ) : null}
        </div>
        {!selectedId ? (
          <AlphaLabEmpty>选择一条训练任务查看训练曲线</AlphaLabEmpty>
        ) : detailQuery.isLoading ? (
          <AlphaLabLoading label="加载训练曲线" />
        ) : !selectedRun ? (
          <AlphaLabEmpty>暂无训练曲线</AlphaLabEmpty>
        ) : !curve.length ? (
          <AlphaLabEmpty>
            该任务暂无训练曲线数据（训练开始后会按步累积每个 step 的指标）
          </AlphaLabEmpty>
        ) : (
          <div className="alphalab-chart-grid">
            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <AlphaLabLabel zh="奖励与得分" en="Reward & Score" />
                </span>
              </div>
              {hasScoreChart ? (
                <div className="alphalab-chart alphalab-chart-small">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={curve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                      <XAxis dataKey="step" minTickGap={32} fontSize={11} />
                      <YAxis width={64} fontSize={11} />
                      <Tooltip />
                      <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
                      {hasRewardCurve ? (
                        <Line
                          type="monotone"
                          dataKey="reward"
                          name="奖励"
                          stroke={CURVE_COLORS.reward}
                          strokeWidth={2}
                          dot={false}
                          connectNulls
                        />
                      ) : null}
                      {hasValidationCurve ? (
                        <Line
                          type="monotone"
                          dataKey="validation_score"
                          name="验证得分"
                          stroke={CURVE_COLORS.validation}
                          strokeWidth={1.8}
                          dot={false}
                          connectNulls
                        />
                      ) : null}
                      {hasBestCurve ? (
                        <Line
                          type="monotone"
                          dataKey="best_score"
                          name="最优得分"
                          stroke={CURVE_COLORS.best}
                          strokeWidth={1.8}
                          dot={false}
                          connectNulls
                        />
                      ) : null}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <AlphaLabEmpty>该任务暂未上报奖励与验证得分</AlphaLabEmpty>
              )}
              <AlphaLabNote>
                横轴是训练步（step），每一步对应一次策略采样与评估。
                <strong>奖励</strong>是当前批量样本的平均奖励；
                <strong>验证得分</strong>是这批样本在验证集上的平均得分；
                <strong>最优得分</strong>是历史最优验证得分，只会上升或持平，
                因此呈阶梯状。
              </AlphaLabNote>
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <AlphaLabLabel zh="信息系数与熵" en="IC / Rank IC / Entropy" />
                </span>
              </div>
              {hasCoefficientChart ? (
                <div className="alphalab-chart alphalab-chart-small">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={curve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                      <XAxis dataKey="step" minTickGap={32} fontSize={11} />
                      <YAxis yAxisId="coefficient" width={64} fontSize={11} />
                      {hasEntropyCurve ? (
                        <YAxis
                          yAxisId="entropy"
                          orientation="right"
                          width={52}
                          fontSize={11}
                        />
                      ) : null}
                      <Tooltip />
                      <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
                      {hasIcCurve ? (
                        <Line
                          yAxisId="coefficient"
                          type="monotone"
                          dataKey="ic"
                          name="信息系数 IC"
                          stroke={CURVE_COLORS.ic}
                          strokeWidth={2}
                          dot={false}
                          connectNulls
                        />
                      ) : null}
                      {hasRankIcCurve ? (
                        <Line
                          yAxisId="coefficient"
                          type="monotone"
                          dataKey="rank_ic"
                          name="排序信息系数 Rank IC"
                          stroke={CURVE_COLORS.rankIc}
                          strokeWidth={1.8}
                          dot={false}
                          connectNulls
                        />
                      ) : null}
                      {hasEntropyCurve ? (
                        <Line
                          yAxisId="entropy"
                          type="monotone"
                          dataKey="entropy"
                          name="熵"
                          stroke={CURVE_COLORS.entropy}
                          strokeWidth={1.8}
                          dot={false}
                          connectNulls
                        />
                      ) : null}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <AlphaLabEmpty>该任务暂未上报信息系数与熵</AlphaLabEmpty>
              )}
              <AlphaLabNote>
                横轴同样是训练步（step）。<strong>信息系数（IC）</strong>与
                <strong>排序信息系数（Rank IC）</strong>
                衡量因子输出与后续收益的相关程度，数值在 0 附近表示几乎没有线性关系；
                <strong>熵（entropy）</strong>
                是策略分布的随机程度，走低说明策略逐渐收敛到少数候选上，
                长期贴近 0 时通常意味着探索不足。熵使用右侧坐标轴。
              </AlphaLabNote>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <ScrollText size={15} />
            <AlphaLabLabel zh="训练日志" en="Training Log" />
          </span>
          {logs.length ? <span className="tag">{logs.length} 行</span> : null}
        </div>
        {!selectedId ? (
          <AlphaLabEmpty>选择一条训练任务查看训练日志</AlphaLabEmpty>
        ) : detailQuery.isLoading ? (
          <AlphaLabLoading label="加载训练日志" />
        ) : !selectedRun ? (
          <AlphaLabEmpty>暂无训练日志</AlphaLabEmpty>
        ) : logs.length ? (
          <>
            <div className="alphalab-log-view" ref={logViewRef}>
              {logs.map((entry, index) => {
                const level = logLevel(entry.level);
                return (
                  <div
                    className={`alphalab-log-line ${level}`}
                    key={`${entry.ts}-${index}`}
                  >
                    <span className="alphalab-log-ts">{formatDateTime(entry.ts)}</span>
                    <span className="alphalab-log-level">{level}</span>
                    <span className="alphalab-log-message">
                      {entry.step !== null && entry.step !== undefined ? (
                        <span className="alphalab-log-step">[step {entry.step}] </span>
                      ) : null}
                      {entry.message}
                    </span>
                  </div>
                );
              })}
            </div>
            <AlphaLabNote>
              日志按时间从旧到新排列；任务处于排队中或运行中时，会自动滚动到最新一行，
              向上滚动可以回看历史。
            </AlphaLabNote>
          </>
        ) : (
          <AlphaLabEmpty>该任务暂无训练日志</AlphaLabEmpty>
        )}
      </div>

      <AlphaLabHelp
        title="因子训练说明"
        items={[
          {
            heading: '训练曲线怎么看',
            body: (
              <ul>
                <li>
                  <strong>奖励（reward）</strong>
                  ：当前批量样本的平均奖励，反映这一步采样出的策略在本轮评估里的整体表现。
                </li>
                <li>
                  <strong>验证得分（validation_score）</strong>
                  ：这批样本在验证集上的平均得分，是判断训练是否在变好的主要参考。
                </li>
                <li>
                  <strong>最优得分（best_score）</strong>
                  ：历史最优的验证得分，只会上升或持平，所以曲线呈阶梯状。
                </li>
                <li>
                  <strong>熵（entropy）</strong>
                  ：策略分布的随机程度。持续下降说明策略逐渐收敛到少数候选上；长期贴近 0
                  往往意味着探索不足。
                </li>
              </ul>
            ),
          },
          {
            heading: '训练日志怎么看',
            body: (
              <ul>
                <li>
                  <strong>时间</strong>：日志写入时间。
                </li>
                <li>
                  <strong>级别</strong>：<code>info</code> 为普通进展，
                  <code>warn</code> 为需要注意，<code>error</code> 为出错。
                </li>
                <li>
                  <strong>内容</strong>：这一步发生了什么；带{' '}
                  <code>[step n]</code> 前缀时表示该行属于第 n 步。
                </li>
                <li>任务失败时先看最后一条 error 行，再看它上方的 warn 行，通常能定位到原因。</li>
              </ul>
            ),
          },
          {
            heading: '训练任务状态说明',
            body: (
              <ul>
                <li>
                  <strong>排队中</strong>（PENDING / QUEUED）：已提交，等待执行器空出线程。
                </li>
                <li>
                  <strong>运行中</strong>（RUNNING）：正在逐步采样与评估，进度、曲线与日志会持续更新。
                </li>
                <li>
                  <strong>成功</strong>（SUCCEEDED）：跑完全部步数，结果写入候选策略与检查点。
                </li>
                <li>
                  <strong>失败</strong>（FAILED）：中途出错，先看日志里的 error 行；可调整数据集或步数后重新提交。
                </li>
                <li>
                  <strong>已取消</strong>（CANCELLED / STOPPED）：被手动取消或停止，之后不会再继续训练。
                </li>
                <li>只有排队中与运行中的任务可以点「取消」。</li>
              </ul>
            ),
          },
          {
            heading: '数据与步数的注意事项',
            body: (
              <ul>
                <li>
                  训练至少需要 {TRAINING_MIN_BARS} 根 bar；数据集不足时前端会提示，后端也会拒绝。
                </li>
                <li>默认使用日周期（1d）数据集；分钟周期数据集不用于训练，仅用于盘中多周期分析。</li>
                <li>步数（steps）与批量（batch）越大，单次训练耗时越长；建议先用小步数确认流程能跑通。</li>
                <li>同一数据集可以创建多个训练任务，任务之间互不影响。</li>
              </ul>
            ),
          },
        ]}
      />
    </div>
  );
}

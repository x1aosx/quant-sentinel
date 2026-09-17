import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Ban,
  BrainCircuit,
  CheckCircle2,
  Database,
  Gauge,
  Plus,
  Rocket,
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
  errorText,
  formatCompact,
  formatDateTime,
  formatNumber,
  progressValue,
  shortId,
} from '../../components/alphalab/format';
import type { DatasetSummary } from '../../types';
import type { TrainingRun } from '../../types/alphalab/training';
import { formatTimeframeLabel } from '../../utils/datasetDisplay';
import '../../styles/alphalab.css';

const ACTIVE_STATUSES = new Set(['PENDING', 'QUEUED', 'RUNNING']);

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

function datasetOptionLabel(dataset: DatasetSummary): string {
  const title = dataset.title?.trim();
  const name = title || dataset.symbol;
  const symbol = title && title !== dataset.symbol ? ` · ${dataset.symbol}` : '';
  return `${name}${symbol} · ${formatTimeframeLabel(dataset.timeframe)} · ${dataset.bar_count} 根`;
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

export function TrainingPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<TrainingFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedId, setSelectedId] = useState('');

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
  const datasets = datasetsQuery.data?.items ?? [];
  const selectedDataset = datasets.find(
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
    if (!datasets.length) {
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
                  const dataset = datasets.find(
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
                  datasets.length === 0
                }
              >
                <option value="">
                  {datasetsQuery.isLoading
                    ? '正在加载数据集...'
                    : datasetsQuery.isError
                      ? '数据集加载失败'
                      : datasets.length
                        ? '请选择数据集'
                        : '暂无可用数据集'}
                </option>
                {datasets.map((dataset) => (
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
              datasets.length === 0 ? (
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
            任务详情
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
                <div className="label">任务 ID</div>
                <div className="value code">{selectedRun.id}</div>
              </div>
              <div className="meta-item">
                <div className="label">Market</div>
                <div className="value">{selectedRun.market || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">Symbols</div>
                <div className="value">{selectedRun.symbols?.join(', ') || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">Schema</div>
                <div className="value">{selectedRun.factor_schema_version || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">Snapshot</div>
                <div className="value code">{selectedRun.data_snapshot_id || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">Worker</div>
                <div className="value">{selectedRun.worker_id || '--'}</div>
              </div>
            </div>

            <div className="alphalab-detail-metrics">
              <AlphaLabMetric
                label="总进度"
                value={`${formatNumber(runProgress(selectedRun), 1)}%`}
                detail={
                  selectedRun.total_steps
                    ? `${selectedRun.current_step ?? 0}/${selectedRun.total_steps}`
                    : `Step ${selectedRun.current_step ?? selectedRun.step ?? 0}`
                }
              />
              <AlphaLabMetric
                label="Loss"
                value={formatNumber(selectedRun.metrics_json?.loss)}
              />
              <AlphaLabMetric
                label="Reward"
                value={formatNumber(selectedRun.metrics_json?.reward)}
              />
              <AlphaLabMetric
                label="Entropy"
                value={formatNumber(selectedRun.metrics_json?.entropy)}
              />
              <AlphaLabMetric
                label="IC"
                value={formatNumber(selectedRun.metrics_json?.ic)}
              />
              <AlphaLabMetric
                label="Turnover"
                value={formatNumber(selectedRun.metrics_json?.turnover)}
              />
            </div>

            <div className="alphalab-formula">
              <div className="label">Best Formula</div>
              <div className="code">{runFormula(selectedRun)}</div>
            </div>

            {selectedRun.error_message ? (
              <div className="alphalab-inline-error">{selectedRun.error_message}</div>
            ) : null}

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <CheckCircle2 size={15} />
                  Top Candidates
                </span>
              </div>
              {selectedRun.candidates?.length ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>策略</th>
                        <th>公式</th>
                        <th>Train</th>
                        <th>Validation</th>
                        <th>总分</th>
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

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Database size={15} />
                  Checkpoint
                </span>
              </div>
              <div className="code">{selectedRun.checkpoint_uri || '--'}</div>
            </div>
          </div>
        ) : (
          <AlphaLabEmpty>暂无训练详情</AlphaLabEmpty>
        )}
      </div>
    </div>
  );
}

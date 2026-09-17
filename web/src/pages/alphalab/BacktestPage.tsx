import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Activity,
  BarChart3,
  Coins,
  FlaskConical,
  Gauge,
  Play,
  TrendingDown,
} from 'lucide-react';
import { backtestApi } from '../../api/alphalab/backtests';
import { strategyApi } from '../../api/alphalab/strategies';
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
  formatDateTime,
  formatNumber,
  formatPercent,
  shortId,
} from '../../components/alphalab/format';
import type {
  BacktestCreateRequest,
  BacktestRun,
} from '../../types/alphalab/backtest';
import type { StrategyArtifact } from '../../types/alphalab/strategy';
import '../../styles/alphalab.css';

interface BacktestFormState {
  name: string;
  strategyId: string;
  strategyVersion: string;
  dataSnapshotId: string;
  startDate: string;
  endDate: string;
  initialCapital: string;
  commissionPct: string;
  slippagePct: string;
  minExposure: string;
}

const DEFAULT_FORM: BacktestFormState = {
  name: '',
  strategyId: '',
  strategyVersion: '',
  dataSnapshotId: '',
  startDate: '',
  endDate: '',
  initialCapital: '1000000',
  commissionPct: '0.0003',
  slippagePct: '0.0002',
  minExposure: '0.05',
};

function strategyOptionValue(strategy: StrategyArtifact): string {
  return JSON.stringify([strategy.id, strategy.version]);
}

function strategyStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    CANDIDATE: '候选',
    VALIDATED: '已验证',
    PRODUCTION: '生产中',
    DEPRECATED: '已弃用',
    REJECTED: '已拒绝',
  };
  return labels[status.toUpperCase()] ?? status;
}

function strategyOptionLabel(strategy: StrategyArtifact): string {
  const symbols =
    strategy.symbols?.filter(Boolean).join(', ') ||
    strategy.symbol_scope ||
    '未指定标的';
  const version = strategy.version ? `v${strategy.version}` : '版本未知';
  return [
    strategy.name || strategy.id,
    symbols,
    version,
    strategyStatusLabel(strategy.status),
  ].join(' · ');
}

interface ChartPoint {
  timestamp: string;
  equity: number;
  drawdown: number;
  rollingSharpe: number | null;
}

function chartTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

function buildChartData(run?: BacktestRun | null): ChartPoint[] {
  if (!run?.equity_curve?.length) return [];
  const drawdownByTime = new Map(
    (run.drawdown_curve ?? []).map((point) => [point.timestamp, point.drawdown]),
  );
  const sharpeByTime = new Map(
    (run.rolling_sharpe ?? []).map((point) => [point.timestamp, point.rolling_sharpe]),
  );
  return run.equity_curve.flatMap((point) =>
    point.equity === undefined
      ? []
      : [
          {
            timestamp: chartTime(point.timestamp),
            equity: point.equity,
            drawdown:
              point.drawdown ?? drawdownByTime.get(point.timestamp) ?? 0,
            rollingSharpe:
              point.rolling_sharpe ??
              sharpeByTime.get(point.timestamp) ??
              null,
          },
        ],
  );
}

function returnRows(value?: Record<string, number>): Array<[string, number]> {
  return Object.entries(value ?? {}).sort(([left], [right]) =>
    left.localeCompare(right),
  );
}

export function BacktestPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BacktestFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedId, setSelectedId] = useState('');

  const overviewQuery = useQuery({
    queryKey: ['alphalab', 'backtest', 'overview'],
    queryFn: backtestApi.overview,
    refetchInterval: 15_000,
  });
  const strategiesQuery = useQuery({
    queryKey: ['alphalab', 'strategy', 'list'],
    queryFn: strategyApi.list,
  });
  const runsQuery = useQuery({
    queryKey: ['alphalab', 'backtest', 'runs'],
    queryFn: backtestApi.list,
    refetchInterval: 8_000,
  });

  const strategies = strategiesQuery.data ?? [];
  const runs = runsQuery.data ?? [];
  const selectedStrategy =
    strategies.find(
      (strategy) =>
        strategy.id === form.strategyId &&
        strategy.version === form.strategyVersion,
    ) ?? null;
  const selected = runs.find((run) => run.id === selectedId) ?? null;
  const selectedMetrics = selected?.metrics_json ?? {};
  const chartData = useMemo(() => buildChartData(selected), [selected]);
  const queryError =
    overviewQuery.error ?? strategiesQuery.error ?? runsQuery.error;
  const updatedAt = Math.max(
    overviewQuery.dataUpdatedAt,
    strategiesQuery.dataUpdatedAt,
    runsQuery.dataUpdatedAt,
  );
  const activeRuns = runs.filter((run) =>
    ['PENDING', 'QUEUED', 'RUNNING'].includes(String(run.status).toUpperCase()),
  ).length;
  const completedRuns = runs.filter((run) =>
    ['SUCCEEDED', 'SUCCESS'].includes(String(run.status).toUpperCase()),
  ).length;

  const createMutation = useMutation({
    mutationFn: backtestApi.create,
    onSuccess: async (run) => {
      setForm(DEFAULT_FORM);
      setFormError('');
      setActionMessage(`回测任务已创建：${run.id}`);
      setSelectedId(run.id);
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'backtest'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const refresh = () => {
    void overviewQuery.refetch();
    void strategiesQuery.refetch();
    void runsQuery.refetch();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError('');
    setActionMessage('');
    if (!selectedStrategy) {
      setFormError(
        strategies.length ? '请选择已有策略' : '暂无可选策略，无法创建回测',
      );
      return;
    }
    const initialCapital = Number(form.initialCapital);
    const commissionPct = Number(form.commissionPct);
    const slippagePct = Number(form.slippagePct);
    const minExposure = Number(form.minExposure);
    if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
      setFormError('初始资金必须大于 0');
      return;
    }
    if ([commissionPct, slippagePct, minExposure].some(
      (value) => !Number.isFinite(value) || value < 0,
    )) {
      setFormError('成本与 min exposure 必须是非负数');
      return;
    }
    if (form.startDate && form.endDate && form.startDate > form.endDate) {
      setFormError('开始日期不能晚于结束日期');
      return;
    }
    const payload: BacktestCreateRequest = {
      name: form.name.trim() || undefined,
      strategy_id: selectedStrategy.id,
      strategy_version: selectedStrategy.version || undefined,
      data_snapshot_id: form.dataSnapshotId.trim() || undefined,
      start_date: form.startDate || undefined,
      end_date: form.endDate || undefined,
      initial_capital: initialCapital,
      commission_pct: commissionPct,
      slippage_pct: slippagePct,
      min_exposure: minExposure,
    };
    createMutation.mutate(payload);
  };

  return (
    <div className="stack alphalab-page">
      <div className="page-header">
        <div>
          <h1>回测研究</h1>
          <div className="row">
            <AlphaLabStatus status={selected?.status} />
            <span className="tag">{runs.length} 个回测</span>
          </div>
        </div>
        <AlphaLabQueryStatus
          isFetching={
            overviewQuery.isFetching ||
            strategiesQuery.isFetching ||
            runsQuery.isFetching
          }
          isError={Boolean(queryError)}
          updatedAt={updatedAt}
          onRefresh={refresh}
        />
      </div>

      {actionMessage ? <div className="notice">{actionMessage}</div> : null}
      {queryError ? <AlphaLabError error={queryError} onRetry={refresh} /> : null}

      <div className="alphalab-metric-grid">
        <div className="panel">
          <AlphaLabMetric
            label="运行中回测"
            value={
              activeRuns ||
              overviewQuery.data?.running_backtests ||
              overviewQuery.data?.backtests?.recent?.filter(
                (run) =>
                  String(run.status ?? '').toUpperCase() === 'RUNNING',
              ).length ||
              0
            }
          />
        </div>
        <div className="panel">
          <AlphaLabMetric label="已完成" value={completedRuns} />
        </div>
        <div className="panel">
          <AlphaLabMetric
            label="总收益"
            value={formatPercent(selectedMetrics.total_return)}
            detail={selected?.id ? shortId(selected.id) : '--'}
          />
        </div>
        <div className="panel">
          <AlphaLabMetric
            label="Sharpe"
            value={formatNumber(selectedMetrics.sharpe)}
            detail={selected ? selected.strategy_version || selected.strategy_id : '--'}
          />
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <FlaskConical size={15} />
            新建回测
          </span>
        </div>
        <form onSubmit={submit}>
          <div className="form-grid alphalab-form-grid">
            <div className="field">
              <label htmlFor="alpha-backtest-name">名称</label>
              <input
                id="alpha-backtest-name"
                value={form.name}
                onChange={(event) =>
                  setForm((current) => ({ ...current, name: event.target.value }))
                }
                placeholder="可选"
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-strategy">策略</label>
              {strategiesQuery.isLoading ? (
                <>
                  <select id="alpha-backtest-strategy" disabled>
                    <option>加载策略中...</option>
                  </select>
                  <span className="muted">正在加载已有策略列表</span>
                </>
              ) : strategiesQuery.isError ? (
                <>
                  <select id="alpha-backtest-strategy" disabled>
                    <option>策略列表加载失败</option>
                  </select>
                  <span className="error-text">请刷新页面后重试</span>
                </>
              ) : strategies.length ? (
                <>
                  <select
                    id="alpha-backtest-strategy"
                    value={
                      selectedStrategy
                        ? strategyOptionValue(selectedStrategy)
                        : ''
                    }
                    onChange={(event) => {
                      const strategy =
                        strategies.find(
                          (item) =>
                            strategyOptionValue(item) === event.target.value,
                        ) ?? null;
                      setForm((current) => ({
                        ...current,
                        strategyId: strategy?.id ?? '',
                        strategyVersion: strategy?.version ?? '',
                      }));
                    }}
                    disabled={createMutation.isPending}
                  >
                    <option value="">请选择已有策略</option>
                    {strategies.map((strategy, index) => (
                      <option
                        key={`${strategyOptionValue(strategy)}:${index}`}
                        value={strategyOptionValue(strategy)}
                      >
                        {strategyOptionLabel(strategy)}
                      </option>
                    ))}
                  </select>
                  {!selectedStrategy ? (
                    <span className="muted">请选择策略后再开始回测</span>
                  ) : null}
                </>
              ) : (
                <>
                  <select id="alpha-backtest-strategy" disabled>
                    <option>暂无可选策略</option>
                  </select>
                  <span className="muted">
                    请先在策略资产中导入或训练策略
                  </span>
                </>
              )}
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-snapshot">数据快照</label>
              <input
                id="alpha-backtest-snapshot"
                value={form.dataSnapshotId}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    dataSnapshotId: event.target.value,
                  }))
                }
                placeholder="可选"
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-start">开始日期</label>
              <input
                id="alpha-backtest-start"
                type="date"
                value={form.startDate}
                onChange={(event) =>
                  setForm((current) => ({ ...current, startDate: event.target.value }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-end">结束日期</label>
              <input
                id="alpha-backtest-end"
                type="date"
                value={form.endDate}
                onChange={(event) =>
                  setForm((current) => ({ ...current, endDate: event.target.value }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-capital">初始资金</label>
              <input
                id="alpha-backtest-capital"
                type="number"
                min="0"
                step="1000"
                value={form.initialCapital}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    initialCapital: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-commission">佣金比例</label>
              <input
                id="alpha-backtest-commission"
                type="number"
                min="0"
                step="0.0001"
                value={form.commissionPct}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    commissionPct: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-slippage">滑点比例</label>
              <input
                id="alpha-backtest-slippage"
                type="number"
                min="0"
                step="0.0001"
                value={form.slippagePct}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    slippagePct: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-backtest-exposure">Min Exposure</label>
              <input
                id="alpha-backtest-exposure"
                type="number"
                min="0"
                step="0.01"
                value={form.minExposure}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    minExposure: event.target.value,
                  }))
                }
              />
            </div>
          </div>
          {formError ? <div className="error-text">{formError}</div> : null}
          <div className="row alphalab-form-actions">
            <button
              type="submit"
              className="button button-primary"
              disabled={
                createMutation.isPending ||
                strategiesQuery.isLoading ||
                Boolean(strategiesQuery.isError) ||
                !selectedStrategy
              }
            >
              <Play size={14} />
              {createMutation.isPending ? '提交中' : '开始回测'}
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <BarChart3 size={15} />
            回测任务
          </span>
          <span className="tag">{runs.length} 条</span>
        </div>
        {runsQuery.isLoading ? (
          <AlphaLabLoading label="加载回测任务" />
        ) : runsQuery.isError ? (
          <AlphaLabError error={runsQuery.error} onRetry={() => void runsQuery.refetch()} />
        ) : runs.length ? (
          <div className="table-wrap">
            <table className="table alphalab-table">
              <thead>
                <tr>
                  <th>回测</th>
                  <th>策略</th>
                  <th>状态</th>
                  <th>总收益</th>
                  <th>年化</th>
                  <th>Sharpe</th>
                  <th>Max DD</th>
                  <th>交易</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    className={selectedId === run.id ? 'selected-row' : undefined}
                    onClick={() => setSelectedId(run.id)}
                  >
                    <td>
                      <div className="alphalab-cell-main">{run.name || shortId(run.id)}</div>
                      <div className="code">{shortId(run.id)}</div>
                    </td>
                    <td className="code">
                      {run.strategy_version || shortId(run.strategy_id)}
                    </td>
                    <td>
                      <AlphaLabStatus status={run.status} />
                    </td>
                    <td>{formatPercent(run.metrics_json?.total_return)}</td>
                    <td>{formatPercent(run.metrics_json?.annualized_return)}</td>
                    <td>{formatNumber(run.metrics_json?.sharpe)}</td>
                    <td>{formatPercent(run.metrics_json?.max_drawdown)}</td>
                    <td>{run.metrics_json?.trade_count ?? run.trades?.length ?? '--'}</td>
                    <td>{formatDateTime(run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <AlphaLabEmpty>暂无回测任务</AlphaLabEmpty>
        )}
      </div>

      <div className="panel alphalab-detail-panel">
        <div className="section-title">
          <span className="section-title-main">
            <Gauge size={15} />
            回测详情
          </span>
          {selected ? <AlphaLabStatus status={selected.status} /> : null}
        </div>
        {!selectedId ? (
          <AlphaLabEmpty>选择一条回测查看详情</AlphaLabEmpty>
        ) : selected ? (
          <div className="stack compact">
            <div className="inline-meta">
              <div className="meta-item">
                <div className="label">回测 ID</div>
                <div className="value code">{selected.id}</div>
              </div>
              <div className="meta-item">
                <div className="label">Strategy</div>
                <div className="value code">{selected.strategy_id}</div>
              </div>
              <div className="meta-item">
                <div className="label">Snapshot</div>
                <div className="value code">{selected.data_snapshot_id || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">开始</div>
                <div className="value">{formatDateTime(selected.started_at)}</div>
              </div>
              <div className="meta-item">
                <div className="label">结束</div>
                <div className="value">{formatDateTime(selected.finished_at)}</div>
              </div>
              <div className="meta-item">
                <div className="label">Artifact</div>
                <div className="value code">{selected.artifact_uri || '--'}</div>
              </div>
            </div>

            <div className="alphalab-detail-metrics">
              <AlphaLabMetric
                label="Total Return"
                value={formatPercent(selectedMetrics.total_return)}
              />
              <AlphaLabMetric
                label="Annual Return"
                value={formatPercent(selectedMetrics.annualized_return)}
              />
              <AlphaLabMetric
                label="Volatility"
                value={formatPercent(selectedMetrics.annualized_volatility)}
              />
              <AlphaLabMetric label="Sharpe" value={formatNumber(selectedMetrics.sharpe)} />
              <AlphaLabMetric label="Sortino" value={formatNumber(selectedMetrics.sortino)} />
              <AlphaLabMetric
                label="Max Drawdown"
                value={formatPercent(selectedMetrics.max_drawdown)}
              />
              <AlphaLabMetric label="Calmar" value={formatNumber(selectedMetrics.calmar)} />
              <AlphaLabMetric
                label="Win Rate"
                value={formatPercent(selectedMetrics.win_rate)}
              />
              <AlphaLabMetric
                label="P/L Ratio"
                value={formatNumber(selectedMetrics.profit_loss_ratio)}
              />
              <AlphaLabMetric
                label="Turnover"
                value={formatNumber(selectedMetrics.turnover)}
              />
            </div>

            {selected.error_message ? (
              <div className="alphalab-inline-error">{selected.error_message}</div>
            ) : null}

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Activity size={15} />
                  Equity Curve
                </span>
              </div>
              {chartData.length ? (
                <div className="alphalab-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                      <XAxis dataKey="timestamp" minTickGap={32} fontSize={11} />
                      <YAxis
                        yAxisId="equity"
                        width={72}
                        fontSize={11}
                        tickFormatter={(value: number) => value.toLocaleString('zh-CN')}
                      />
                      <Tooltip />
                      <Line
                        yAxisId="equity"
                        type="monotone"
                        dataKey="equity"
                        name="Equity"
                        stroke="#17746c"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <AlphaLabEmpty>暂无权益曲线</AlphaLabEmpty>
              )}
            </div>

            <div className="alphalab-detail-columns">
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">
                    <TrendingDown size={15} />
                    Drawdown
                  </span>
                </div>
                {chartData.length ? (
                  <div className="alphalab-chart alphalab-chart-small">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                        <XAxis dataKey="timestamp" minTickGap={32} fontSize={11} />
                        <YAxis
                          width={60}
                          fontSize={11}
                          tickFormatter={(value: number) =>
                            `${(value * 100).toFixed(0)}%`
                          }
                        />
                        <Tooltip />
                        <Area
                          type="monotone"
                          dataKey="drawdown"
                          name="Drawdown"
                          stroke="#b42318"
                          fill="#fdeceb"
                          strokeWidth={1.5}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <AlphaLabEmpty>暂无回撤曲线</AlphaLabEmpty>
                )}
              </div>

              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">
                    <Activity size={15} />
                    Rolling Sharpe
                  </span>
                </div>
                {chartData.some((point) => point.rollingSharpe !== null) ? (
                  <div className="alphalab-chart alphalab-chart-small">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                        <XAxis dataKey="timestamp" minTickGap={32} fontSize={11} />
                        <YAxis width={48} fontSize={11} />
                        <Tooltip />
                        <Line
                          type="monotone"
                          dataKey="rollingSharpe"
                          name="Rolling Sharpe"
                          stroke="#345b8c"
                          strokeWidth={1.8}
                          dot={false}
                          connectNulls
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <AlphaLabEmpty>暂无 Rolling Sharpe</AlphaLabEmpty>
                )}
              </div>
            </div>

            <div className="alphalab-detail-columns">
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">Monthly Returns</span>
                </div>
                {returnRows(selected.monthly_returns).length ? (
                  <div className="alphalab-return-list">
                    {returnRows(selected.monthly_returns).map(([key, value]) => (
                      <div key={key}>
                        <span>{key}</span>
                        <strong className={value >= 0 ? 'positive' : 'negative'}>
                          {formatPercent(value)}
                        </strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <AlphaLabEmpty>暂无月度收益</AlphaLabEmpty>
                )}
              </div>

              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">Annual Returns</span>
                </div>
                {returnRows(selected.annual_returns).length ? (
                  <div className="alphalab-return-list">
                    {returnRows(selected.annual_returns).map(([key, value]) => (
                      <div key={key}>
                        <span>{key}</span>
                        <strong className={value >= 0 ? 'positive' : 'negative'}>
                          {formatPercent(value)}
                        </strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <AlphaLabEmpty>暂无年度收益</AlphaLabEmpty>
                )}
              </div>
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Coins size={15} />
                  Trades
                </span>
                <span className="tag">{selected.trades?.length ?? 0} 笔</span>
              </div>
              {selected.trades?.length ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>标的</th>
                        <th>方向</th>
                        <th>入场</th>
                        <th>出场</th>
                        <th>数量</th>
                        <th>PnL</th>
                        <th>收益</th>
                        <th>持仓 Bars</th>
                        <th>成本</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.trades.map((trade, index) => (
                        <tr key={trade.id || index}>
                          <td>{trade.symbol || '--'}</td>
                          <td>{trade.side || '--'}</td>
                          <td>{formatDateTime(trade.entry_time)}</td>
                          <td>{formatDateTime(trade.exit_time)}</td>
                          <td>{formatNumber(trade.quantity, 2)}</td>
                          <td className={(trade.pnl ?? 0) >= 0 ? 'positive' : 'negative'}>
                            {formatNumber(trade.pnl, 2)}
                          </td>
                          <td>{formatPercent(trade.return_pct)}</td>
                          <td>{formatNumber(trade.holding_bars, 1)}</td>
                          <td>{formatNumber(trade.cost, 4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <AlphaLabEmpty>暂无交易记录</AlphaLabEmpty>
              )}
            </div>

            <div className="alphalab-detail-columns">
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">Costs</span>
                </div>
                {Object.keys(selected.cost_breakdown ?? {}).length ? (
                  <div className="alphalab-return-list">
                    {Object.entries(selected.cost_breakdown ?? {}).map(([key, value]) => (
                      <div key={key}>
                        <span>{key}</span>
                        <strong>{formatNumber(value, 4)}</strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <AlphaLabEmpty>暂无成本明细</AlphaLabEmpty>
                )}
              </div>

              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">Per Symbol</span>
                </div>
                {Object.keys(selected.per_symbol_metrics ?? {}).length ? (
                  <div className="alphalab-return-list">
                    {Object.entries(selected.per_symbol_metrics ?? {}).map(
                      ([symbol, metrics]) => (
                        <div key={symbol}>
                          <span>{symbol}</span>
                          <strong>
                            {formatPercent(metrics.total_return)} / Sharpe{' '}
                            {formatNumber(metrics.sharpe)}
                          </strong>
                        </div>
                      ),
                    )}
                  </div>
                ) : (
                  <AlphaLabEmpty>暂无分标的数据</AlphaLabEmpty>
                )}
              </div>
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">Robustness</span>
              </div>
              {selected.robustness_json ? (
                <pre className="alphalab-json">
                  {JSON.stringify(selected.robustness_json, null, 2)}
                </pre>
              ) : (
                <AlphaLabEmpty>暂无稳健性结果</AlphaLabEmpty>
              )}
            </div>
          </div>
        ) : (
          <AlphaLabEmpty>暂无回测详情</AlphaLabEmpty>
        )}
      </div>
    </div>
  );
}

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
  ShieldCheck,
  Table2,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { backtestApi } from '../../api/alphalab/backtests';
import { strategyApi } from '../../api/alphalab/strategies';
import {
  AlphaLabHelp,
  AlphaLabLabel,
  AlphaLabNote,
  type AlphaLabHelpItem,
} from '../../components/alphalab/AlphaLabHelp';
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
  BacktestMetrics,
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

type TimeAxisKind = 'session' | 'bar_index';

interface ChartPoint {
  timestamp: string;
  equity: number;
  drawdown: number | null;
  rollingSharpe: number | null;
}

const TIME_AXIS_LABELS: Record<TimeAxisKind, string> = {
  session: '交易日',
  bar_index: 'Bar 序号',
};

function timeAxisKind(run?: BacktestRun | null): TimeAxisKind {
  return run?.time_axis_kind === 'session' ? 'session' : 'bar_index';
}

/** `session` 轴是真实交易会话，缩成 `MM-DD`（带分钟时再补时间）；Bar 序号原样显示。 */
function axisTickLabel(value: string, kind: TimeAxisKind): string {
  if (kind !== 'session') return value;
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(value);
  if (!match) return value;
  const [, , month, day, hour, minute] = match;
  return hour && minute ? `${month}-${day} ${hour}:${minute}` : `${month}-${day}`;
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
            timestamp: point.timestamp,
            equity: point.equity,
            drawdown:
              point.drawdown ?? drawdownByTime.get(point.timestamp) ?? null,
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

function hasNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

const METRIC_KEYS: Array<keyof BacktestMetrics> = [
  'total_return',
  'annualized_return',
  'sharpe',
  'sortino',
  'calmar',
  'max_drawdown',
  'win_rate',
  'profit_loss_ratio',
  'trade_count',
];

/** 后端可能整块返回空指标，这时不要假装有绩效数据。 */
function hasMetrics(metrics?: BacktestMetrics | null): boolean {
  return METRIC_KEYS.some((key) => hasNumber(metrics?.[key]));
}

interface PerfCard {
  zh: string;
  en: string;
  value: string;
  detail: string;
  tone?: 'positive' | 'negative';
}

function perfCards(metrics: BacktestMetrics): PerfCard[] {
  const totalReturnTone =
    hasNumber(metrics.total_return) && metrics.total_return < 0
      ? 'negative'
      : hasNumber(metrics.total_return)
        ? 'positive'
        : undefined;
  return [
    {
      zh: '总收益',
      en: 'Total Return',
      value: formatPercent(metrics.total_return),
      detail: `年化 ${formatPercent(metrics.annualized_return)}`,
      tone: totalReturnTone,
    },
    {
      zh: '夏普比率',
      en: 'Sharpe',
      value: formatNumber(metrics.sharpe, 2),
      detail: `波动率 ${formatPercent(metrics.annualized_volatility)}`,
    },
    {
      zh: '索提诺比率',
      en: 'Sortino',
      value: formatNumber(metrics.sortino, 2),
      detail: '只惩罚下行波动',
    },
    {
      zh: '卡玛比率',
      en: 'Calmar',
      value: formatNumber(metrics.calmar, 2),
      detail: '年化收益 / 最大回撤',
    },
    {
      zh: '盈亏比',
      en: 'Profit-Loss Ratio',
      value: formatNumber(metrics.profit_loss_ratio, 2),
      detail: `盈亏因子 ${formatNumber(metrics.profit_factor, 2)}`,
    },
    {
      zh: '胜率',
      en: 'Win Rate',
      value: formatPercent(metrics.win_rate),
      detail: `${formatNumber(metrics.win_count, 0)} 盈 / ${formatNumber(
        metrics.loss_count,
        0,
      )} 亏`,
    },
    {
      zh: '交易次数',
      en: 'Trades',
      value: formatNumber(metrics.trade_count, 0),
      detail: `平均持仓 ${formatNumber(metrics.average_holding_bars, 1)} Bar`,
    },
    {
      zh: '最大回撤',
      en: 'Max Drawdown',
      value: formatPercent(metrics.max_drawdown),
      detail: `平均换手 ${formatNumber(metrics.average_turnover, 2)}`,
    },
  ];
}

function sideLabel(side?: string): { text: string; tone: 'long' | 'short' | 'flat' } {
  const normalized = (side ?? '').trim().toLowerCase();
  if (normalized === 'long') return { text: '做多', tone: 'long' };
  if (normalized === 'short') return { text: '做空', tone: 'short' };
  return { text: side || '--', tone: 'flat' };
}

function tradeSession(session?: string, fallback?: string): string {
  return session || fallback || '--';
}

const HELP_ITEMS: AlphaLabHelpItem[] = [
  {
    heading: '回测是怎么跑的',
    body: (
      <>
        <p>
          回测把策略信号按时间顺序喂给撮合引擎，用历史行情逐根 K 线推进，模拟下单、持仓和结算。
        </p>
        <ul>
          <li>
            只用<strong>已收盘的 K 线</strong>算信号：第 t 根收盘后得到的目标仓位，最早在第 t+1
            根开盘成交（<code>execution_lag_bars = 1</code>），不会用到未来数据。
          </li>
          <li>
            按 A 股约束处理：默认开启 <strong>T+1</strong>（当天买入不能当天卖出），默认
            <strong>禁止做空</strong>（做空信号被拦成空仓），单标的敞口上限 100%。
          </li>
          <li>
            成本逐笔计入：<strong>佣金</strong>按成交额比例收取、<strong>印花税</strong>
            只在卖出侧计收、<strong>过户费</strong>与<strong>滑点</strong>同样计入；佣金和滑点来自新建回测表单。
          </li>
          <li>权益每根 K 线按收盘结算，曲线上的每个点就是那根 K 线收盘时的账户权益。</li>
        </ul>
      </>
    ),
  },
  {
    heading: '绩效指标怎么读',
    body: (
      <>
        <ul>
          <li>
            <strong>总收益 / 年化收益</strong>：区间累计收益与折算到一年的收益率；区间越短，年化越容易被放大。
          </li>
          <li>
            <strong>夏普比率</strong>：单位总波动换来的收益，越高说明收益相对波动更平稳；它对上涨和下跌波动一视同仁。
          </li>
          <li>
            <strong>索提诺比率</strong>：只用下行波动做惩罚，更贴近「怕亏」的直觉。
          </li>
          <li>
            <strong>卡玛比率</strong>：年化收益 ÷ 最大回撤，衡量为了这份收益要承受多深的回撤。
          </li>
          <li>
            <strong>盈亏比 / 盈亏因子</strong>：平均盈利 ÷ 平均亏损；盈亏因子是总盈利 ÷ 总亏损。
          </li>
          <li>
            <strong>胜率</strong>：盈利笔数占比；胜率高不等于赚钱，还要看盈亏比和成本。
          </li>
          <li>
            <strong>最大回撤</strong>：从历史最高点回落的最深幅度，是最直观的「最难熬时刻」。
          </li>
          <li>
            <strong>指标好看不等于未来能赚</strong>：这些数字只描述这段历史，样本区间、成本假设和参数都会改变结果。
          </li>
        </ul>
      </>
    ),
  },
  {
    heading: '资金曲线怎么看',
    body: (
      <>
        <ul>
          <li>
            <strong>累计净值</strong>：账户权益随时间的变化，回答「这段时间一共赚了多少、曲线陡不陡」。
          </li>
          <li>
            <strong>回撤曲线</strong>：当前权益距历史最高点的回落百分比，回答「最难受的时候亏了多少」。
          </li>
          <li>
            <strong>滚动夏普</strong>：在一个滚动窗口（日线默认约 21 根 K 线）内算出的年化夏普，回答「收益是稳定的还是集中在少数几段」；窗口还没填满时该点按 0 处理。
          </li>
          <li>
            <strong>回撤越深越久越难恢复</strong>：跌 50% 需要涨 100% 才回本，所以曲线的形状和最大回撤一样重要。
          </li>
          <li>
            x 轴按数据来源标注：有真实交易会话时显示「交易日」，只有合成 Bar 时显示「Bar 序号」，不会编造日期。
          </li>
        </ul>
      </>
    ),
  },
  {
    heading: '为什么回测和实盘不一样',
    body: (
      <>
        <ul>
          <li>
            <strong>滑点与冲击成本</strong>：回测按固定比例估算滑点，真实成交价还受流动性、盘口深度和下单方式影响。
          </li>
          <li>
            <strong>成交假设</strong>：回测假设下一根开盘能按假设价格成交，实盘可能排队、部分成交甚至错过。
          </li>
          <li>
            <strong>样本内外差异</strong>：训练和验证用过的数据再拿来回测会偏乐观，真正未知的只有未来。
          </li>
          <li>
            <strong>参数过拟合</strong>：在历史上反复调参挑出的「最优组合」，往往只对那段历史最优。
          </li>
          <li>所以回测只适合用来比较方案和排查逻辑问题，不构成任何收益承诺。</li>
        </ul>
      </>
    ),
  },
];

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
  const axisKind = timeAxisKind(selected);
  const axisLabel = TIME_AXIS_LABELS[axisKind];
  const hasRollingSharpe = chartData.some((point) => point.rollingSharpe !== null);
  const perSymbolRows = Object.entries(selected?.per_symbol_metrics ?? {}).sort(
    ([left], [right]) => left.localeCompare(right),
  );
  const finalEquity = selected?.final_equity ?? selectedMetrics.final_equity;
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

            {selected.error_message ? (
              <div className="alphalab-inline-error">{selected.error_message}</div>
            ) : null}

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Gauge size={15} />
                  回测绩效
                </span>
                {chartData.length ? (
                  <span className="tag">区间 {chartData.length} 根 K 线</span>
                ) : null}
              </div>
              {hasMetrics(selectedMetrics) ? (
                <>
                  <div className="alphalab-perf-grid">
                    {perfCards(selectedMetrics).map((card) => (
                      <div className="alphalab-perf-card" key={card.zh}>
                        <div className="alphalab-perf-label">
                          <AlphaLabLabel zh={card.zh} en={card.en} />
                        </div>
                        <div
                          className={
                            card.tone
                              ? `alphalab-perf-value ${card.tone}`
                              : 'alphalab-perf-value'
                          }
                        >
                          {card.value}
                        </div>
                        <div className="alphalab-perf-detail">{card.detail}</div>
                      </div>
                    ))}
                  </div>
                  <AlphaLabNote>
                    指标来自这条回测的区间结果，已按逐笔成本（佣金 / 印花税 / 过户费 / 滑点）扣减；
                    总收益的绿色 / 红色只表示正负，不代表任何预期。
                  </AlphaLabNote>
                </>
              ) : (
                <AlphaLabEmpty>暂无绩效指标</AlphaLabEmpty>
              )}
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Table2 size={15} />
                  绩效明细
                </span>
              </div>
              {perSymbolRows.length || hasMetrics(selectedMetrics) ? (
                <>
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>
                            <AlphaLabLabel zh="品种" en="Symbol" />
                          </th>
                          <th>
                            <AlphaLabLabel zh="收益" en="Return" />
                          </th>
                          <th>
                            <AlphaLabLabel zh="夏普" en="Sharpe" />
                          </th>
                          <th>
                            <AlphaLabLabel zh="索提诺" en="Sortino" />
                          </th>
                          <th>
                            <AlphaLabLabel zh="盈亏比" en="P/L" />
                          </th>
                          <th>
                            <AlphaLabLabel zh="交易数" en="Trades" />
                          </th>
                          <th>
                            <AlphaLabLabel zh="胜率" en="Win Rate" />
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>
                            <AlphaLabLabel zh="组合" en="Portfolio" />
                          </td>
                          <td>{formatPercent(selectedMetrics.total_return)}</td>
                          <td>{formatNumber(selectedMetrics.sharpe, 2)}</td>
                          <td>{formatNumber(selectedMetrics.sortino, 2)}</td>
                          <td>
                            {formatNumber(selectedMetrics.profit_loss_ratio, 2)}
                          </td>
                          <td>{formatNumber(selectedMetrics.trade_count, 0)}</td>
                          <td>{formatPercent(selectedMetrics.win_rate)}</td>
                        </tr>
                        {perSymbolRows.map(([symbol, metrics]) => (
                          <tr key={symbol}>
                            <td className="code">{symbol}</td>
                            <td>{formatPercent(metrics.total_return)}</td>
                            <td>{formatNumber(metrics.sharpe, 2)}</td>
                            <td>{formatNumber(metrics.sortino, 2)}</td>
                            <td>{formatNumber(metrics.profit_loss_ratio, 2)}</td>
                            <td>{formatNumber(metrics.trade_count, 0)}</td>
                            <td>{formatPercent(metrics.win_rate)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <AlphaLabNote>
                    组合行是整个回测的合并结果；分标的行按标的分别统计，后端未返回的字段显示
                    --（例如分标的口径没有索提诺与盈亏比）。
                  </AlphaLabNote>
                </>
              ) : (
                <AlphaLabEmpty>暂无分标的绩效数据</AlphaLabEmpty>
              )}
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Activity size={15} />
                  资金曲线
                </span>
                <span className="tag">X 轴：{axisLabel}</span>
              </div>

              <div className="alphalab-detail-metrics">
                <div className="alphalab-metric">
                  <div className="stat-label">
                    <AlphaLabLabel zh="总收益" en="Total Return" />
                  </div>
                  <div className="alphalab-metric-value">
                    {formatPercent(selectedMetrics.total_return)}
                  </div>
                </div>
                <div className="alphalab-metric">
                  <div className="stat-label">
                    <AlphaLabLabel zh="夏普" en="Sharpe" />
                  </div>
                  <div className="alphalab-metric-value">
                    {formatNumber(selectedMetrics.sharpe, 2)}
                  </div>
                </div>
                <div className="alphalab-metric">
                  <div className="stat-label">
                    <AlphaLabLabel zh="索提诺" en="Sortino" />
                  </div>
                  <div className="alphalab-metric-value">
                    {formatNumber(selectedMetrics.sortino, 2)}
                  </div>
                </div>
                <div className="alphalab-metric">
                  <div className="stat-label">
                    <AlphaLabLabel zh="最大回撤" en="Max Drawdown" />
                  </div>
                  <div className="alphalab-metric-value">
                    {formatPercent(selectedMetrics.max_drawdown)}
                  </div>
                </div>
                <div className="alphalab-metric">
                  <div className="stat-label">
                    <AlphaLabLabel zh="期末权益" en="Final Equity" />
                  </div>
                  <div className="alphalab-metric-value">
                    {formatNumber(finalEquity, 2)}
                  </div>
                  <div className="alphalab-metric-detail">
                    初始 {formatNumber(
                      selected.initial_equity ?? selectedMetrics.initial_equity,
                      2,
                    )}
                  </div>
                </div>
              </div>

              {chartData.length ? (
                <>
                  <div className="alphalab-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                        <XAxis
                          dataKey="timestamp"
                          minTickGap={32}
                          fontSize={11}
                          tickFormatter={(value: string) =>
                            axisTickLabel(value, axisKind)
                          }
                        />
                        <YAxis
                          width={78}
                          fontSize={11}
                          tickFormatter={(value: number) =>
                            Math.round(value).toLocaleString('zh-CN')
                          }
                        />
                        <Tooltip
                          formatter={(value) => formatNumber(Number(value), 2)}
                        />
                        <Area
                          type="monotone"
                          dataKey="equity"
                          name="累计净值"
                          stroke="#17746c"
                          fill="#e6f2ef"
                          strokeWidth={1.8}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  <AlphaLabNote>
                    累计净值：账户权益随时间的变化，回答「这段时间一共赚了多少、曲线陡不陡」。
                    横轴为{axisLabel}
                    {axisKind === 'session'
                      ? '（真实交易会话）'
                      : '（合成 Bar 序号，没有真实日期）'}
                    。
                  </AlphaLabNote>

                  <div className="alphalab-chart-grid">
                    <div className="alphalab-subsection">
                      <div className="section-title">
                        <span className="section-title-main">
                          <TrendingDown size={15} />
                          回撤
                        </span>
                      </div>
                      <div className="alphalab-chart alphalab-chart-small">
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                            <XAxis
                              dataKey="timestamp"
                              minTickGap={32}
                              fontSize={11}
                              tickFormatter={(value: string) =>
                                axisTickLabel(value, axisKind)
                              }
                            />
                            <YAxis
                              width={56}
                              fontSize={11}
                              tickFormatter={(value: number) =>
                                `${(value * 100).toFixed(0)}%`
                              }
                            />
                            <Tooltip
                              formatter={(value) => formatPercent(Number(value))}
                            />
                            <Area
                              type="monotone"
                              dataKey="drawdown"
                              name="回撤"
                              stroke="#b42318"
                              fill="#fdeceb"
                              strokeWidth={1.5}
                            />
                          </AreaChart>
                        </ResponsiveContainer>
                      </div>
                      <AlphaLabNote>
                        回撤：当前权益距历史最高点的回落百分比，回答「最难受的时候亏了多少」。
                        越深、持续越久，回本需要的涨幅越大。
                      </AlphaLabNote>
                    </div>

                    <div className="alphalab-subsection">
                      <div className="section-title">
                        <span className="section-title-main">
                          <TrendingUp size={15} />
                          滚动夏普
                        </span>
                      </div>
                      {hasRollingSharpe ? (
                        <>
                          <div className="alphalab-chart alphalab-chart-small">
                            <ResponsiveContainer width="100%" height="100%">
                              <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#dce3ea" />
                                <XAxis
                                  dataKey="timestamp"
                                  minTickGap={32}
                                  fontSize={11}
                                  tickFormatter={(value: string) =>
                                    axisTickLabel(value, axisKind)
                                  }
                                />
                                <YAxis width={48} fontSize={11} />
                                <Tooltip
                                  formatter={(value) => formatNumber(Number(value), 2)}
                                />
                                <Line
                                  type="monotone"
                                  dataKey="rollingSharpe"
                                  name="滚动夏普"
                                  stroke="#345b8c"
                                  strokeWidth={1.8}
                                  dot={false}
                                  connectNulls
                                />
                              </LineChart>
                            </ResponsiveContainer>
                          </div>
                          <AlphaLabNote>
                            滚动夏普：在一个滚动窗口（日线默认约 21 根 K 线）内算出的年化夏普，
                            回答「收益是稳定的还是集中在少数几段」；窗口没填满时该点按 0 处理。
                          </AlphaLabNote>
                        </>
                      ) : (
                        <AlphaLabEmpty>暂无滚动夏普数据</AlphaLabEmpty>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <AlphaLabEmpty>暂无资金曲线数据</AlphaLabEmpty>
              )}
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Coins size={15} />
                  交易记录
                </span>
                <span className="tag">{selected.trades?.length ?? 0} 笔</span>
              </div>
              {selected.trades?.length ? (
                <>
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>标的</th>
                          <th>方向</th>
                          <th>入场</th>
                          <th>出场</th>
                          <th>入场价</th>
                          <th>出场价</th>
                          <th>持仓 Bar</th>
                          <th>收益</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selected.trades.map((trade, index) => {
                          const side = sideLabel(trade.side);
                          const profitable = hasNumber(trade.return_pct)
                            ? trade.return_pct >= 0
                            : null;
                          return (
                            <tr key={trade.id || index}>
                              <td>{trade.symbol || '--'}</td>
                              <td>
                                <span className={`alphalab-direction ${side.tone}`}>
                                  {side.text}
                                </span>
                              </td>
                              <td>
                                {tradeSession(trade.entry_session, trade.entry_time)}
                              </td>
                              <td>
                                {tradeSession(trade.exit_session, trade.exit_time)}
                              </td>
                              <td>{formatNumber(trade.entry_price, 2)}</td>
                              <td>{formatNumber(trade.exit_price, 2)}</td>
                              <td>{formatNumber(trade.holding_bars, 0)}</td>
                              <td
                                className={
                                  profitable === null
                                    ? undefined
                                    : profitable
                                      ? 'positive'
                                      : 'negative'
                                }
                              >
                                {formatPercent(trade.return_pct)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <AlphaLabNote>
                    入场 / 出场显示交易会话；数据源只有合成 Bar 时退化为 Bar 序号。收益为单笔收益率，
                    已扣该笔成交的成本。
                  </AlphaLabNote>
                </>
              ) : (
                <AlphaLabEmpty>暂无交易记录</AlphaLabEmpty>
              )}
            </div>

            <div className="alphalab-detail-columns">
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">月度收益</span>
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
                  <span className="section-title-main">年度收益</span>
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

            <div className="alphalab-detail-columns">
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">
                    <Coins size={15} />
                    成本明细
                  </span>
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
                  <span className="section-title-main">
                    <ShieldCheck size={15} />
                    稳健性
                  </span>
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
          </div>
        ) : (
          <AlphaLabEmpty>暂无回测详情</AlphaLabEmpty>
        )}
      </div>

      <AlphaLabHelp title="回测怎么看" items={HELP_ITEMS} />
    </div>
  );
}

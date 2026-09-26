import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  CircleDot,
  Gauge,
  Play,
  Plus,
  Radar,
  Trash2,
  Zap,
} from 'lucide-react';
import { realtimeApi } from '../../api/alphalab/realtime';
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
  shortId,
} from '../../components/alphalab/format';
import type {
  RealtimeSignal,
  RealtimeWatch,
  RealtimeWatchCreateRequest,
} from '../../types/alphalab/realtime';
import type { StrategyArtifact } from '../../types/alphalab/strategy';
import '../../styles/alphalab.css';

interface WatchFormState {
  source: string;
  symbol: string;
  timeframe: string;
  strategyId: string;
  strategyVersion: string;
  enabled: boolean;
}

interface WatchGroup {
  key: string;
  source: string;
  symbol: string;
  watches: RealtimeWatch[];
}

/** 一个可监控组合：策略在训练时绑定的品种 + 周期。 */
interface MonitorTarget {
  key: string;
  symbol: string;
  timeframe: string;
  strategy: StrategyArtifact;
}

const DEFAULT_FORM: WatchFormState = {
  source: 'market_data',
  symbol: '',
  timeframe: '',
  strategyId: '',
  strategyVersion: '',
  enabled: true,
};

const TIMEFRAME_MS: Record<string, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '15m': 15 * 60_000,
  '30m': 30 * 60_000,
  '1h': 60 * 60_000,
  '60m': 60 * 60_000,
  '4h': 4 * 60 * 60_000,
  '1d': 24 * 60 * 60_000,
  '1w': 7 * 24 * 60 * 60_000,
};

const STRATEGY_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  CANDIDATE: '候选',
  VALIDATED: '已验证',
  PRODUCTION: '生产中',
  DEPRECATED: '已弃用',
  REJECTED: '已拒绝',
};

function strategyStatusLabel(status?: string | null): string {
  const value = String(status ?? '').toUpperCase();
  return STRATEGY_STATUS_LABELS[value] ?? (value || '状态未知');
}

function strategyVersionLabel(strategy: StrategyArtifact): string {
  return strategy.version ? `v${strategy.version}` : '版本未知';
}

function targetKey(strategy: StrategyArtifact, symbol: string): string {
  return `${symbol}::${strategy.id}::${strategy.version}`;
}

function strategyTargetValue(target: MonitorTarget): string {
  return `${target.strategy.id}::${target.strategy.version}`;
}

function targetLabel(target: MonitorTarget): string {
  return [
    target.symbol,
    target.timeframe,
    strategyStatusLabel(target.strategy.status),
    `${target.strategy.name || target.strategy.id} ${strategyVersionLabel(target.strategy)}`,
  ].join(' · ');
}

function strategyLabel(target: MonitorTarget): string {
  return [
    `${target.strategy.name || target.strategy.id} ${strategyVersionLabel(target.strategy)}`,
    target.timeframe,
    strategyStatusLabel(target.strategy.status),
  ].join(' · ');
}

function directionTone(direction?: string | null): string {
  switch (String(direction ?? '').toUpperCase()) {
    case 'LONG':
      return 'long';
    case 'SHORT':
      return 'short';
    default:
      return 'flat';
  }
}

function directionText(direction?: string | null): string {
  switch (String(direction ?? '').toUpperCase()) {
    case 'LONG':
      return '预期上涨';
    case 'SHORT':
      return '预期下跌';
    default:
      return '观望';
  }
}

function DirectionBadge({ direction }: { direction?: string | null }) {
  const value = direction || 'FLAT';
  return (
    <span className={`alphalab-direction ${directionTone(value)}`}>
      <CircleDot size={12} />
      {value}
    </span>
  );
}

/** 因子接近 0 视为无信号，其余档位按 last_strength（等于 |tanh(因子)|，范围 0..1）分档。 */
function confidenceLabel(
  lastFactor?: number | null,
  lastStrength?: number | null,
): string {
  if (Math.abs(lastFactor ?? 0) < 0.05) return '无信号';
  const strength = Math.abs(lastStrength ?? 0);
  if (strength < 0.25) return '把握不大';
  if (strength < 0.5) return '一半把握';
  if (strength < 0.75) return '比较有把握';
  return '很有把握';
}

function confidencePercent(lastStrength?: number | null): number {
  const value = (lastStrength ?? 0) * 100;
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/** 距离下一根 K 线收盘的剩余毫秒；时间戳或周期无法解析时返回 null。 */
function countdownMs(watch: RealtimeWatch, now: number): number | null {
  const interval = TIMEFRAME_MS[watch.timeframe];
  if (!interval || !watch.last_closed_bar_ts) return null;
  const closedAt = new Date(watch.last_closed_bar_ts).getTime();
  if (Number.isNaN(closedAt)) return null;
  return Math.max(0, closedAt + interval - now);
}

function countdownText(remaining: number | null): string {
  if (remaining === null) return '距离下次判断 --';
  const totalSeconds = Math.floor(remaining / 1_000);
  const days = Math.floor(totalSeconds / 86_400);
  const hours = Math.floor((totalSeconds % 86_400) / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  // 长周期用「天/时」表述，避免 1d 监控出现 863分59秒 这种读数。
  if (days > 0) {
    return `距离下次判断 ${days}天${String(hours).padStart(2, '0')}时`;
  }
  if (hours > 0) {
    return `距离下次判断 ${hours}时${String(minutes).padStart(2, '0')}分`;
  }
  return `距离下次判断 ${minutes}分${String(seconds).padStart(2, '0')}秒`;
}

const HELP_ITEMS: AlphaLabHelpItem[] = [
  {
    heading: '这张卡怎么看',
    body: (
      <>
        卡片顶部的颜色来自方向：预期上涨为多头方向，预期下跌为空头方向，观望表示因子接近 0。
        中间的「把握」由因子的绝对值换算而来（|tanh(因子)|，取值 0 到 1），只说明策略对这个方向有多坚决，
        <strong>不是胜率</strong>，也不表示一定会盈利；进度条长度与把握程度同步。
        下面的因子、仓位、强度是策略最近一次评估输出的原始数值，收盘前不会变化。
      </>
    ),
  },
  {
    heading: '为什么只在收盘后才重新判断',
    body: (
      <>
        评估只使用已经收盘的 K 线。盘中未收盘的 K 线还在变化，用它算出的因子会来回跳动；
        同一根 K 线收盘后数据固定下来，信号才随之稳定。所以卡片上显示的是「最后收盘K线」，
        而不是当前正在走的这一根。
      </>
    ),
  },
  {
    heading: '为什么必须选择已有策略的标的',
    body: (
      <>
        策略与训练时的品种、周期绑定：因子表达式、阈值和仓位规则都是在那个品种和周期上拟合与验证的。
        把同一份策略套到别的品种或别的周期上，等于换了一个未经验证的假设，需要重新训练或单独验证。
        因此新增监控只能从已训练策略的标的与周期里选，不能自由填写。
      </>
    ),
  },
  {
    heading: '距离下次判断在倒计时什么',
    body: (
      <>
        倒计时指向下一根 K 线收盘、可以重新判断信号的时刻，按「最后收盘K线 + 周期长度」计算，到 0 表示新的 K 线
        应该已经收盘。倒计时本身不会触发重新评估：需要等页面的定时刷新或手动点「评估」之后，才会出现新的结果。
        时间戳无法解析时显示 --。
      </>
    ),
  },
];

export function LiveMonitorPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<WatchFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedWatchId, setSelectedWatchId] = useState('');
  const [signalFilter, setSignalFilter] = useState('');
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const overviewQuery = useQuery({
    queryKey: ['alphalab', 'realtime', 'overview'],
    queryFn: realtimeApi.overview,
    refetchInterval: 15_000,
  });
  const watchesQuery = useQuery({
    queryKey: ['alphalab', 'realtime', 'watches'],
    queryFn: realtimeApi.listWatches,
    refetchInterval: 5_000,
  });
  const signalsQuery = useQuery({
    queryKey: ['alphalab', 'realtime', 'signals'],
    queryFn: () => realtimeApi.listSignals({ limit: 200 }),
    refetchInterval: 8_000,
  });
  const strategiesQuery = useQuery({
    queryKey: ['alphalab', 'strategy', 'list'],
    queryFn: strategyApi.list,
  });

  const watches = watchesQuery.data ?? [];
  const signals = signalsQuery.data ?? [];
  const strategies = strategiesQuery.data ?? [];
  const selectedWatch =
    watches.find((watch) => watch.id === selectedWatchId) ?? null;
  const queryError =
    overviewQuery.error ?? watchesQuery.error ?? signalsQuery.error;
  const updatedAt = Math.max(
    overviewQuery.dataUpdatedAt,
    watchesQuery.dataUpdatedAt,
    signalsQuery.dataUpdatedAt,
  );

  // 只有策略训练时绑定的（品种, 周期）组合才允许创建监控项，后端会校验同样的规则。
  const targets = useMemo<MonitorTarget[]>(() => {
    const items: MonitorTarget[] = [];
    const seen = new Set<string>();
    strategies.forEach((strategy) => {
      const timeframe = String(strategy.timeframe ?? '').trim();
      if (!timeframe) return;
      const symbols = (
        strategy.symbols?.length
          ? strategy.symbols
          : strategy.symbol_scope
            ? [strategy.symbol_scope]
            : []
      )
        .map((symbol) => String(symbol).trim())
        .filter(Boolean);
      symbols.forEach((symbol) => {
        const key = targetKey(strategy, symbol);
        if (seen.has(key)) return;
        seen.add(key);
        items.push({ key, symbol, timeframe, strategy });
      });
    });
    return items.sort(
      (left, right) =>
        left.symbol.localeCompare(right.symbol) ||
        left.timeframe.localeCompare(right.timeframe) ||
        left.strategy.name.localeCompare(right.strategy.name),
    );
  }, [strategies]);

  const selectedTarget = useMemo(
    () =>
      targets.find(
        (target) =>
          target.symbol === form.symbol &&
          target.strategy.id === form.strategyId &&
          target.strategy.version === form.strategyVersion,
      ) ?? null,
    [form.strategyId, form.strategyVersion, form.symbol, targets],
  );

  const symbolTargets = useMemo(
    () => targets.filter((target) => target.symbol === form.symbol),
    [form.symbol, targets],
  );

  const groups = useMemo(() => {
    const grouped = new Map<string, WatchGroup>();
    watches.forEach((watch) => {
      const key = `${watch.source}:${watch.symbol}`;
      const current = grouped.get(key);
      if (current) {
        current.watches.push(watch);
      } else {
        grouped.set(key, {
          key,
          source: watch.source,
          symbol: watch.symbol,
          watches: [watch],
        });
      }
    });
    return Array.from(grouped.values()).sort((left, right) =>
      left.symbol.localeCompare(right.symbol),
    );
  }, [watches]);

  const filteredSignals = useMemo(() => {
    const keyword = signalFilter.trim().toLowerCase();
    if (!keyword) return signals;
    return signals.filter((signal) =>
      [
        signal.symbol,
        signal.timeframe,
        signal.direction,
        signal.strategy_id,
        signal.strategy_version,
        signal.strategy_name,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword)),
    );
  }, [signalFilter, signals]);

  const enabledWatchList = watches.filter((watch) => watch.enabled);
  const enabledWatches = enabledWatchList.length;
  const activeWatches = watches.filter((watch) =>
    ['RUNNING', 'ACTIVE', 'SUCCESS', 'SUCCEEDED'].includes(
      String(watch.state).toUpperCase(),
    ),
  ).length;
  const errorWatches = watches.filter((watch) => watch.last_error || watch.error).length;

  const formLocked =
    strategiesQuery.isLoading || strategiesQuery.isError || !targets.length;

  const createMutation = useMutation({
    mutationFn: realtimeApi.createWatch,
    onSuccess: async (watch) => {
      setForm(DEFAULT_FORM);
      setFormError('');
      setActionMessage(`监控项已创建：${watch.id}`);
      setSelectedWatchId(watch.id);
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'realtime'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: realtimeApi.deleteWatch,
    onSuccess: async (_result, id) => {
      setActionMessage(`监控项已删除：${id}`);
      if (selectedWatchId === id) setSelectedWatchId('');
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'realtime'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const evaluateMutation = useMutation({
    mutationFn: realtimeApi.evaluate,
    onSuccess: async (result) => {
      const evaluated = result.evaluated ?? 0;
      const generated = result.generated ?? result.signals?.length ?? 0;
      setFormError('');
      setActionMessage(`评估 ${evaluated} 项，生成 ${generated} 条信号`);
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'realtime'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const refresh = () => {
    void overviewQuery.refetch();
    void watchesQuery.refetch();
    void signalsQuery.refetch();
    void strategiesQuery.refetch();
  };

  const selectTarget = (key: string) => {
    const target = targets.find((item) => item.key === key);
    setForm((current) => ({
      ...current,
      symbol: target?.symbol ?? '',
      timeframe: target?.timeframe ?? '',
      strategyId: target?.strategy.id ?? '',
      strategyVersion: target?.strategy.version ?? '',
    }));
  };

  const selectStrategy = (value: string) => {
    const target = symbolTargets.find(
      (item) => strategyTargetValue(item) === value,
    );
    if (!target) return;
    setForm((current) => ({
      ...current,
      // 周期必须跟随所选策略，后端按策略训练时的周期校验。
      timeframe: target.timeframe,
      strategyId: target.strategy.id,
      strategyVersion: target.strategy.version,
    }));
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError('');
    setActionMessage('');
    if (!selectedTarget) {
      setFormError(
        targets.length
          ? '请选择已有策略对应的监控标的'
          : '暂无可监控标的：请先训练或导入与品种、周期绑定的策略',
      );
      return;
    }
    const payload: RealtimeWatchCreateRequest = {
      source: form.source,
      symbol: selectedTarget.symbol,
      timeframe: selectedTarget.timeframe,
      strategy_id: selectedTarget.strategy.id,
      strategy_version: selectedTarget.strategy.version || undefined,
      enabled: form.enabled,
    };
    createMutation.mutate(payload);
  };

  const deleteWatch = (watch: RealtimeWatch) => {
    if (!window.confirm(`确定删除 ${watch.symbol} ${watch.timeframe} 的监控项吗？`)) {
      return;
    }
    deleteMutation.mutate(watch.id);
  };

  const evaluate = (watch?: RealtimeWatch | null) => {
    setFormError('');
    setActionMessage('');
    if (watch) {
      evaluateMutation.mutate({ watch_id: watch.id });
      return;
    }
    evaluateMutation.mutate({});
  };

  return (
    <div className="stack alphalab-page">
      <div className="page-header">
        <div>
          <h1>实时监控</h1>
          <div className="row">
            <AlphaLabStatus status={selectedWatch?.state} />
            <span className="tag">{watches.length} 个监控项</span>
          </div>
        </div>
        <AlphaLabQueryStatus
          isFetching={
            overviewQuery.isFetching ||
            watchesQuery.isFetching ||
            signalsQuery.isFetching
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
            label="监控项"
            value={
              overviewQuery.data?.realtime?.watch_count ??
              overviewQuery.data?.realtime_watches ??
              watches.length
            }
            detail={`${enabledWatches} 已启用`}
          />
        </div>
        <div className="panel">
          <AlphaLabMetric label="运行状态" value={activeWatches} detail="活跃监控" />
        </div>
        <div className="panel">
          <AlphaLabMetric label="异常" value={errorWatches} detail="最近错误" />
        </div>
        <div className="panel">
          <AlphaLabMetric
            label="信号变化"
            value={
              overviewQuery.data?.recent_signal_changes ??
              signals.filter((signal) => signal.direction !== 'FLAT').length
            }
            detail="最近记录"
          />
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Gauge size={15} />
            信号雷达
          </span>
          <span className="tag">{enabledWatchList.length} 个启用</span>
        </div>
        {watchesQuery.isLoading ? (
          <AlphaLabLoading label="加载信号雷达" />
        ) : enabledWatchList.length ? (
          <div className="alphalab-radar-grid">
            {enabledWatchList.map((watch) => (
              <div
                key={watch.id}
                className={`alphalab-radar-card ${directionTone(watch.last_direction)}${
                  selectedWatchId === watch.id ? ' selected' : ''
                }`}
                role="button"
                tabIndex={0}
                aria-pressed={selectedWatchId === watch.id}
                onClick={() => setSelectedWatchId(watch.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setSelectedWatchId(watch.id);
                  }
                }}
              >
                <div className="alphalab-radar-head">
                  <div className="alphalab-radar-symbol">
                    <strong>{watch.symbol}</strong>
                    <span className="tag">{watch.timeframe}</span>
                  </div>
                  <AlphaLabStatus status={watch.state} />
                </div>
                <div className="alphalab-radar-body">
                  <div className="alphalab-radar-confidence">
                    <div className="alphalab-radar-confidence-value">
                      {confidenceLabel(watch.last_factor, watch.last_strength)}
                    </div>
                    <div className="alphalab-radar-confidence-track">
                      <div
                        className="alphalab-radar-confidence-fill"
                        style={{ width: `${confidencePercent(watch.last_strength)}%` }}
                      />
                    </div>
                  </div>
                  <div className="stack compact">
                    <span className="muted">{directionText(watch.last_direction)}</span>
                    <DirectionBadge direction={watch.last_direction} />
                  </div>
                </div>
                <div className="alphalab-radar-meta">
                  <div>
                    <span>因子</span>
                    <span>{formatNumber(watch.last_factor)}</span>
                  </div>
                  <div>
                    <span>仓位</span>
                    <span>{formatNumber(watch.last_position)}</span>
                  </div>
                  <div>
                    <span>强度</span>
                    <span>{formatNumber(watch.last_strength)}</span>
                  </div>
                  <div>
                    <span>策略</span>
                    <span>{watch.strategy_name || shortId(watch.strategy_id)}</span>
                  </div>
                  <div>
                    <span>最后收盘K线</span>
                    <span>{formatDateTime(watch.last_closed_bar_ts)}</span>
                  </div>
                </div>
                <div className="alphalab-radar-foot">
                  <span>更新 {formatDateTime(watch.updated_at)}</span>
                  <span className="alphalab-radar-countdown">
                    {countdownText(countdownMs(watch, now))}
                  </span>
                </div>
                {watch.last_error ? (
                  <div className="alphalab-radar-note">
                    该监控最近一次评估失败，结果仅供参考：{watch.last_error}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <AlphaLabEmpty>暂无启用的监控项</AlphaLabEmpty>
        )}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Plus size={15} />
            新增监控
          </span>
          <span className="tag">{targets.length} 个可选组合</span>
        </div>
        {strategiesQuery.isLoading ? (
          <AlphaLabLoading label="加载已有策略" />
        ) : strategiesQuery.isError ? (
          <AlphaLabError
            error={strategiesQuery.error}
            onRetry={() => void strategiesQuery.refetch()}
          />
        ) : targets.length ? null : (
          <AlphaLabEmpty>
            暂无可监控标的：请先在「策略」页训练或导入与品种、周期绑定的策略
          </AlphaLabEmpty>
        )}
        <form onSubmit={submit}>
          <div className="form-grid alphalab-form-grid">
            <div className="field">
              <label htmlFor="alpha-watch-target">
                <AlphaLabLabel zh="监控标的" en="Symbol" />
              </label>
              <select
                id="alpha-watch-target"
                value={selectedTarget?.key ?? ''}
                disabled={formLocked}
                onChange={(event) => selectTarget(event.target.value)}
              >
                <option value="">请选择已有策略的标的</option>
                {targets.map((target) => (
                  <option key={target.key} value={target.key}>
                    {targetLabel(target)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-strategy">
                <AlphaLabLabel zh="策略" en="Strategy" />
              </label>
              <select
                id="alpha-watch-strategy"
                value={selectedTarget ? strategyTargetValue(selectedTarget) : ''}
                disabled={formLocked || !symbolTargets.length}
                onChange={(event) => selectStrategy(event.target.value)}
              >
                <option value="">请选择策略</option>
                {symbolTargets.map((target) => (
                  <option key={target.key} value={strategyTargetValue(target)}>
                    {strategyLabel(target)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-timeframe">
                <AlphaLabLabel zh="周期" en="Timeframe" />
              </label>
              <input
                id="alpha-watch-timeframe"
                value={form.timeframe}
                placeholder="--"
                disabled
                readOnly
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-source">
                <AlphaLabLabel zh="数据源" en="Source" />
              </label>
              <select
                id="alpha-watch-source"
                value={form.source}
                disabled={formLocked}
                onChange={(event) =>
                  setForm((current) => ({ ...current, source: event.target.value }))
                }
              >
                <option value="market_data">数据中心 (market_data)</option>
                <option value="local">本地 (local)</option>
              </select>
            </div>
            <label className="checkbox-row alphalab-checkbox">
              <input
                type="checkbox"
                checked={form.enabled}
                disabled={formLocked}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    enabled: event.target.checked,
                  }))
                }
              />
              启用
            </label>
          </div>
          {formError ? <div className="error-text">{formError}</div> : null}
          <AlphaLabNote>
            监控标的与周期来自已训练策略：策略与训练时的品种、周期绑定，换品种或换周期需要重新训练或单独验证。
          </AlphaLabNote>
          <div className="row alphalab-form-actions">
            <button
              type="submit"
              className="button button-primary"
              disabled={createMutation.isPending || formLocked || !selectedTarget}
            >
              <Plus size={14} />
              {createMutation.isPending ? '创建中' : '添加监控'}
            </button>
            <button
              type="button"
              className="button"
              disabled={evaluateMutation.isPending || !watches.length}
              onClick={() => evaluate()}
            >
              <Zap size={14} />
              {evaluateMutation.isPending ? '评估中' : '评估全部'}
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Radar size={15} />
            监控列表
          </span>
          <span className="tag">{groups.length} 个标的</span>
        </div>
        {watchesQuery.isLoading ? (
          <AlphaLabLoading label="加载实时监控" />
        ) : watchesQuery.isError ? (
          <AlphaLabError
            error={watchesQuery.error}
            onRetry={() => void watchesQuery.refetch()}
          />
        ) : groups.length ? (
          <div className="table-wrap">
            <table className="table alphalab-table alphalab-watch-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>Source</th>
                  <th>周期 / 策略</th>
                  <th>方向 / 强度</th>
                  <th>最后收盘 Bar</th>
                  <th>状态</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <tr key={group.key}>
                    <td>
                      <div className="alphalab-cell-main">{group.symbol}</div>
                      <div className="muted">{group.watches.length} 个周期</div>
                    </td>
                    <td>{group.source}</td>
                    <td>
                      <div className="alphalab-watch-list">
                        {group.watches.map((watch) => (
                          <button
                            type="button"
                            className={
                              selectedWatchId === watch.id
                                ? 'alphalab-watch-item selected'
                                : 'alphalab-watch-item'
                            }
                            key={watch.id}
                            onClick={() => setSelectedWatchId(watch.id)}
                          >
                            <span className="tag">{watch.timeframe}</span>
                            <span>{watch.strategy_name || shortId(watch.strategy_id)}</span>
                          </button>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="alphalab-watch-list">
                        {group.watches.map((watch) => (
                          <div className="alphalab-watch-signal" key={watch.id}>
                            <DirectionBadge direction={watch.last_direction} />
                            <span>{formatNumber(watch.last_strength, 4)}</span>
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="alphalab-watch-list">
                        {group.watches.map((watch) => (
                          <div className="alphalab-watch-time" key={watch.id}>
                            {formatDateTime(watch.last_closed_bar_ts)}
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="alphalab-watch-list">
                        {group.watches.map((watch) => (
                          <div key={watch.id}>
                            <AlphaLabStatus status={watch.state} />
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="alphalab-watch-list">
                        {group.watches.map((watch) => (
                          <div className="alphalab-watch-time" key={watch.id}>
                            {formatDateTime(watch.updated_at)}
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="alphalab-watch-actions">
                        {group.watches.map((watch) => (
                          <button
                            type="button"
                            className="button button-danger"
                            key={watch.id}
                            disabled={deleteMutation.isPending}
                            onClick={() => deleteWatch(watch)}
                          >
                            <Trash2 size={13} />
                            {watch.timeframe}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <AlphaLabEmpty>暂无实时监控项</AlphaLabEmpty>
        )}
      </div>

      <div className="panel alphalab-detail-panel">
        <div className="section-title">
          <span className="section-title-main">
            <Activity size={15} />
            监控详情
          </span>
          {selectedWatch ? <AlphaLabStatus status={selectedWatch.state} /> : null}
        </div>
        {!selectedWatchId ? (
          <AlphaLabEmpty>选择一个监控项查看详情</AlphaLabEmpty>
        ) : selectedWatch ? (
          <div className="stack compact">
            <div className="inline-meta">
              <div className="meta-item">
                <div className="label">Watch ID</div>
                <div className="value code">{selectedWatch.id}</div>
              </div>
              <div className="meta-item">
                <div className="label">Source</div>
                <div className="value">{selectedWatch.source}</div>
              </div>
              <div className="meta-item">
                <div className="label">Symbol</div>
                <div className="value">{selectedWatch.symbol}</div>
              </div>
              <div className="meta-item">
                <div className="label">Timeframe</div>
                <div className="value">{selectedWatch.timeframe}</div>
              </div>
              <div className="meta-item">
                <div className="label">Strategy</div>
                <div className="value code">{selectedWatch.strategy_id}</div>
              </div>
              <div className="meta-item">
                <div className="label">Strategy Version</div>
                <div className="value">{selectedWatch.strategy_version || '--'}</div>
              </div>
            </div>

            <div className="alphalab-detail-metrics">
              <AlphaLabMetric
                label="Direction"
                value={<DirectionBadge direction={selectedWatch.last_direction} />}
              />
              <AlphaLabMetric
                label="Position"
                value={formatNumber(selectedWatch.last_position)}
              />
              <AlphaLabMetric
                label="Strength"
                value={formatNumber(selectedWatch.last_strength)}
              />
              <AlphaLabMetric
                label="Factor"
                value={formatNumber(selectedWatch.last_factor)}
              />
              <AlphaLabMetric
                label="Closed Bar"
                value={formatDateTime(selectedWatch.last_closed_bar_ts)}
              />
              <AlphaLabMetric
                label="Enabled"
                value={selectedWatch.enabled ? '是' : '否'}
              />
            </div>

            {selectedWatch.last_error || selectedWatch.error ? (
              <div className="alphalab-inline-error">
                {selectedWatch.last_error || selectedWatch.error}
              </div>
            ) : null}

            <div className="row">
              <button
                type="button"
                className="button button-primary"
                disabled={evaluateMutation.isPending}
                onClick={() => evaluate(selectedWatch)}
              >
                <Play size={14} />
                立即评估
              </button>
              <button
                type="button"
                className="button button-danger"
                disabled={deleteMutation.isPending}
                onClick={() => deleteWatch(selectedWatch)}
              >
                <Trash2 size={14} />
                删除监控
              </button>
            </div>
          </div>
        ) : (
          <AlphaLabEmpty>监控项不存在</AlphaLabEmpty>
        )}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Zap size={15} />
            最近信号
          </span>
          <input
            className="alphalab-signal-filter"
            value={signalFilter}
            onChange={(event) => setSignalFilter(event.target.value)}
            placeholder="筛选标的、周期、方向"
          />
        </div>
        {signalsQuery.isLoading ? (
          <AlphaLabLoading label="加载信号记录" />
        ) : signalsQuery.isError ? (
          <AlphaLabError
            error={signalsQuery.error}
            onRetry={() => void signalsQuery.refetch()}
          />
        ) : filteredSignals.length ? (
          <div className="table-wrap">
            <table className="table alphalab-table">
              <thead>
                <tr>
                  <th>Bar Close</th>
                  <th>标的</th>
                  <th>周期</th>
                  <th>策略</th>
                  <th>方向</th>
                  <th>Factor</th>
                  <th>Position</th>
                  <th>Strength</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {filteredSignals.map((signal: RealtimeSignal) => (
                  <tr key={signal.id}>
                    <td>{formatDateTime(signal.bar_close_ts)}</td>
                    <td>{signal.symbol}</td>
                    <td>{signal.timeframe}</td>
                    <td>
                      <div className="alphalab-cell-main">
                        {signal.strategy_name || shortId(signal.strategy_id)}
                      </div>
                      <div className="code">
                        {signal.strategy_version || shortId(signal.strategy_id)}
                      </div>
                    </td>
                    <td>
                      <DirectionBadge direction={signal.direction} />
                    </td>
                    <td>{formatNumber(signal.factor_value)}</td>
                    <td>{formatNumber(signal.position)}</td>
                    <td>{formatNumber(signal.strength)}</td>
                    <td>{formatDateTime(signal.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <AlphaLabEmpty>
            {signalFilter ? '没有匹配的信号' : '暂无信号记录'}
          </AlphaLabEmpty>
        )}
      </div>

      <AlphaLabHelp items={HELP_ITEMS} />
    </div>
  );
}

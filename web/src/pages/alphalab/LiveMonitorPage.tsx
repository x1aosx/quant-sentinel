import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  CircleDot,
  Play,
  Plus,
  Radar,
  Trash2,
  Zap,
} from 'lucide-react';
import { realtimeApi } from '../../api/alphalab/realtime';
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

const DEFAULT_FORM: WatchFormState = {
  source: 'market_data',
  symbol: '',
  timeframe: '1d',
  strategyId: '',
  strategyVersion: '',
  enabled: true,
};

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

function DirectionBadge({ direction }: { direction?: string | null }) {
  const value = direction || 'FLAT';
  return (
    <span className={`alphalab-direction ${directionTone(value)}`}>
      <CircleDot size={12} />
      {value}
    </span>
  );
}

export function LiveMonitorPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<WatchFormState>(DEFAULT_FORM);
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedWatchId, setSelectedWatchId] = useState('');
  const [signalFilter, setSignalFilter] = useState('');

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

  const watches = watchesQuery.data ?? [];
  const signals = signalsQuery.data ?? [];
  const selectedWatch =
    watches.find((watch) => watch.id === selectedWatchId) ?? null;
  const queryError =
    overviewQuery.error ?? watchesQuery.error ?? signalsQuery.error;
  const updatedAt = Math.max(
    overviewQuery.dataUpdatedAt,
    watchesQuery.dataUpdatedAt,
    signalsQuery.dataUpdatedAt,
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

  const enabledWatches = watches.filter((watch) => watch.enabled).length;
  const activeWatches = watches.filter((watch) =>
    ['RUNNING', 'ACTIVE', 'SUCCESS', 'SUCCEEDED'].includes(
      String(watch.state).toUpperCase(),
    ),
  ).length;
  const errorWatches = watches.filter((watch) => watch.last_error || watch.error).length;

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
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError('');
    setActionMessage('');
    if (!form.source.trim()) {
      setFormError('source 不能为空');
      return;
    }
    if (!form.symbol.trim()) {
      setFormError('symbol 不能为空');
      return;
    }
    if (!form.strategyId.trim()) {
      setFormError('strategy_id 不能为空');
      return;
    }
    const payload: RealtimeWatchCreateRequest = {
      source: form.source.trim(),
      symbol: form.symbol.trim().toUpperCase(),
      timeframe: form.timeframe,
      strategy_id: form.strategyId.trim(),
      strategy_version: form.strategyVersion.trim() || undefined,
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
            <Plus size={15} />
            新增监控
          </span>
        </div>
        <form onSubmit={submit}>
          <div className="form-grid alphalab-form-grid">
            <div className="field">
              <label htmlFor="alpha-watch-source">Source</label>
              <input
                id="alpha-watch-source"
                value={form.source}
                onChange={(event) =>
                  setForm((current) => ({ ...current, source: event.target.value }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-symbol">Symbol</label>
              <input
                id="alpha-watch-symbol"
                value={form.symbol}
                onChange={(event) =>
                  setForm((current) => ({ ...current, symbol: event.target.value }))
                }
                placeholder="600519.SH"
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-timeframe">周期</label>
              <select
                id="alpha-watch-timeframe"
                value={form.timeframe}
                onChange={(event) =>
                  setForm((current) => ({ ...current, timeframe: event.target.value }))
                }
              >
                <option value="1d">1d</option>
                <option value="1h">1h</option>
                <option value="30m">30m</option>
                <option value="15m">15m</option>
                <option value="5m">5m</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-strategy">Strategy ID</label>
              <input
                id="alpha-watch-strategy"
                value={form.strategyId}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    strategyId: event.target.value,
                  }))
                }
              />
            </div>
            <div className="field">
              <label htmlFor="alpha-watch-version">策略版本</label>
              <input
                id="alpha-watch-version"
                value={form.strategyVersion}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    strategyVersion: event.target.value,
                  }))
                }
                placeholder="可选"
              />
            </div>
            <label className="checkbox-row alphalab-checkbox">
              <input
                type="checkbox"
                checked={form.enabled}
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
          <div className="row alphalab-form-actions">
            <button
              type="submit"
              className="button button-primary"
              disabled={createMutation.isPending}
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
    </div>
  );
}

import type { AlphaLabOverview } from '../../types/alphalab/common';
import type {
  BacktestCreateRequest,
  BacktestEquityPoint,
  BacktestMetrics,
  BacktestRobustness,
  BacktestRun,
  BacktestTrade,
} from '../../types/alphalab/backtest';
import {
  alphalabList,
  alphalabRequest,
  finiteNumber,
  optionalString,
  recordValue,
} from './request';

function numberArray(value: unknown): number[] {
  return (Array.isArray(value) ? value : []).flatMap((item) => {
    const parsed = finiteNumber(item);
    return parsed === undefined ? [] : [parsed];
  });
}

/**
 * The backend returns aligned numeric series (`equity_curve`, `drawdown_curve`,
 * `rolling_sharpe`) plus a `time_axis`. Older payloads returned objects, so both
 * shapes are accepted and merged into a single point-per-bar series.
 */
function buildEquitySeries(
  raw: Record<string, unknown>,
): { points: BacktestEquityPoint[]; axis: string[]; kind: 'session' | 'bar_index' } {
  const axis = Array.isArray(raw.time_axis)
    ? raw.time_axis.map((item) => String(item))
    : [];
  const kind = raw.time_axis_kind === 'session' ? 'session' : 'bar_index';

  const numericEquity = numberArray(raw.equity_curve);
  if (numericEquity.length) {
    const drawdown = numberArray(raw.drawdown_curve);
    const rolling = numberArray(raw.rolling_sharpe);
    return {
      kind,
      axis,
      points: numericEquity.map((equity, index) => ({
        timestamp: axis[index] ?? String(index),
        equity,
        drawdown: drawdown[index],
        rolling_sharpe: rolling[index],
      })),
    };
  }

  const points = (Array.isArray(raw.equity_curve) ? raw.equity_curve : []).flatMap(
    (item, index) => {
      const point = recordValue(item);
      const equity = finiteNumber(point.equity ?? point.value);
      if (equity === undefined) return [];
      return [
        {
          timestamp: String(
            point.timestamp ?? point.time ?? point.session ?? axis[index] ?? index,
          ),
          equity,
          drawdown: finiteNumber(point.drawdown),
          rolling_sharpe: finiteNumber(point.rolling_sharpe),
        },
      ];
    },
  );
  return { points, axis, kind };
}

function normalizeMetrics(value: unknown): BacktestMetrics {
  const raw = recordValue(value);
  const result: BacktestMetrics = { ...raw };
  for (const key of [
    'initial_equity',
    'final_equity',
    'total_return',
    'annualized_return',
    'annualized_volatility',
    'sharpe',
    'sortino',
    'max_drawdown',
    'calmar',
    'win_rate',
    'profit_loss_ratio',
    'profit_factor',
    'average_win',
    'average_loss',
    'best_trade',
    'worst_trade',
    'win_count',
    'loss_count',
    'trade_count',
    'turnover',
    'average_turnover',
    'average_holding_bars',
    'exposure_mean',
    'exposure_max',
  ]) {
    const parsed = finiteNumber(raw[key]);
    if (parsed !== undefined) result[key] = parsed;
  }
  return result;
}

function normalizeTrade(value: unknown): BacktestTrade {
  const raw = recordValue(value);
  const direction = optionalString(raw.direction);
  return {
    ...raw,
    symbol: optionalString(raw.symbol),
    side: optionalString(raw.side) ?? direction,
    direction,
    entry_bar: finiteNumber(raw.entry_bar),
    exit_bar: finiteNumber(raw.exit_bar),
    entry_time: optionalString(raw.entry_time),
    exit_time: optionalString(raw.exit_time),
    entry_session: optionalString(raw.entry_session),
    exit_session: optionalString(raw.exit_session),
    entry_price: finiteNumber(raw.entry_price),
    exit_price: finiteNumber(raw.exit_price),
    quantity: finiteNumber(raw.quantity),
    pnl: finiteNumber(raw.pnl),
    return_pct: finiteNumber(raw.return_pct) ?? finiteNumber(raw.pnl),
    holding_bars: finiteNumber(raw.holding_bars),
    cost: finiteNumber(raw.cost),
  } as BacktestTrade;
}

function normalizeBacktest(value: unknown): BacktestRun {
  const raw = recordValue(value);
  const metrics = normalizeMetrics(raw.metrics_json ?? raw.metrics);
  const robustness = recordValue(
    raw.robustness_json ?? raw.robustness,
  ) as BacktestRobustness;
  const symbols = Array.isArray(raw.per_symbol) ? raw.per_symbol : [];
  const series = buildEquitySeries(raw);
  return {
    ...raw,
    id: String(raw.id ?? raw.run_id ?? ''),
    strategy_id: String(raw.strategy_id ?? ''),
    strategy_version: optionalString(raw.strategy_version),
    status: String(raw.status ?? 'SUCCEEDED').toUpperCase(),
    data_snapshot_id:
      optionalString(raw.data_snapshot_id) ?? optionalString(raw.dataset_id),
    initial_equity:
      finiteNumber(raw.initial_equity) ?? finiteNumber(metrics.initial_equity),
    final_equity:
      finiteNumber(raw.final_equity) ?? finiteNumber(metrics.final_equity),
    metrics_json: {
      ...metrics,
      total_return:
        finiteNumber(metrics.total_return) ?? finiteNumber(raw.total_return),
      annualized_return:
        finiteNumber(metrics.annualized_return) ??
        finiteNumber(raw.annualized_return),
      max_drawdown:
        finiteNumber(metrics.max_drawdown) ?? finiteNumber(raw.max_drawdown),
      sharpe: finiteNumber(metrics.sharpe) ?? finiteNumber(raw.sharpe),
      turnover: finiteNumber(metrics.turnover) ?? finiteNumber(raw.turnover),
      trade_count:
        finiteNumber(metrics.trade_count) ?? finiteNumber(raw.trade_count),
    },
    equity_curve: series.points,
    drawdown_curve: series.points,
    rolling_sharpe: series.points.filter(
      (point) => point.rolling_sharpe !== undefined,
    ),
    time_axis: series.axis,
    time_axis_kind: series.kind,
    monthly_returns: recordValue(
      raw.monthly_returns ?? robustness.monthly_returns,
    ) as Record<string, number>,
    annual_returns: recordValue(
      raw.annual_returns ?? robustness.annual_returns,
    ) as Record<string, number>,
    per_symbol_metrics: Object.fromEntries(
      symbols.flatMap((item) => {
        const symbolResult = recordValue(item);
        const symbol = optionalString(symbolResult.symbol);
        return symbol
          ? [[symbol, normalizeMetrics(symbolResult.metrics)] as const]
          : [];
      }),
    ),
    trades: (Array.isArray(raw.trades)
      ? raw.trades
      : Array.isArray(raw.trade_log)
        ? raw.trade_log
        : []
    ).map(normalizeTrade),
    cost_breakdown: recordValue(raw.cost_breakdown) as Record<string, number>,
    robustness_json: robustness,
    created_at: optionalString(raw.created_at),
  } as BacktestRun;
}

export const backtestApi = {
  overview: () => alphalabRequest<AlphaLabOverview>('/alphalab/overview'),
  list: () =>
    alphalabRequest<unknown>('/alphalab/backtests')
      .then(alphalabList<unknown>)
      .then((items) => items.map(normalizeBacktest)),
  create: (payload: BacktestCreateRequest) =>
    alphalabRequest<unknown>('/alphalab/backtests', {
      method: 'POST',
      body: JSON.stringify(payload),
    }).then(normalizeBacktest),
};

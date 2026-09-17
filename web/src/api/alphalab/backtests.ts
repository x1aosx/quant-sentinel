import type { AlphaLabOverview } from '../../types/alphalab/common';
import type {
  BacktestCreateRequest,
  BacktestRun,
} from '../../types/alphalab/backtest';
import type {
  BacktestEquityPoint,
  BacktestMetrics,
  BacktestTrade,
} from '../../types/alphalab/backtest';
import {
  alphalabList,
  alphalabRequest,
  finiteNumber,
  optionalString,
  recordValue,
} from './request';

function normalizeCurve(value: unknown, key: 'equity' | 'drawdown'): BacktestEquityPoint[] {
  return (Array.isArray(value) ? value : []).flatMap((item, index) => {
    if (typeof item === 'number') {
      return [{ timestamp: String(index), [key]: item } as BacktestEquityPoint];
    }
    const point = recordValue(item);
    const timestamp = String(
      point.timestamp ?? point.time ?? point.datetime ?? point.date ?? index,
    );
    if (key === 'equity') {
      const equity = finiteNumber(point.equity ?? point.value);
      return equity === undefined
        ? []
        : [{ timestamp, equity, drawdown: finiteNumber(point.drawdown) }];
    }
    return [
      {
        timestamp,
        equity: finiteNumber(point.equity),
        drawdown: finiteNumber(point.drawdown) ?? finiteNumber(point.value),
      },
    ];
  });
}

function normalizeMetrics(value: unknown): BacktestMetrics {
  const raw = recordValue(value);
  const result: BacktestMetrics = { ...raw };
  for (const key of [
    'total_return',
    'annualized_return',
    'annualized_volatility',
    'sharpe',
    'sortino',
    'max_drawdown',
    'calmar',
    'win_rate',
    'profit_loss_ratio',
    'trade_count',
    'turnover',
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
  return {
    ...raw,
    symbol: optionalString(raw.symbol),
    side: optionalString(raw.side) ?? optionalString(raw.direction),
    direction: optionalString(raw.direction),
    entry_time: optionalString(raw.entry_time),
    exit_time: optionalString(raw.exit_time),
    entry_price: finiteNumber(raw.entry_price),
    exit_price: finiteNumber(raw.exit_price),
    quantity: finiteNumber(raw.quantity),
    pnl: finiteNumber(raw.pnl),
    return_pct: finiteNumber(raw.return_pct),
    holding_bars: finiteNumber(raw.holding_bars),
    cost: finiteNumber(raw.cost),
  } as BacktestTrade;
}

function normalizeBacktest(value: unknown): BacktestRun {
  const raw = recordValue(value);
  const metrics = normalizeMetrics(raw.metrics_json ?? raw.metrics);
  const robustness = recordValue(raw.robustness_json ?? raw.robustness);
  const symbols = Array.isArray(raw.per_symbol) ? raw.per_symbol : [];
  return {
    ...raw,
    id: String(raw.id ?? raw.run_id ?? ''),
    strategy_id: String(raw.strategy_id ?? ''),
    strategy_version: optionalString(raw.strategy_version),
    status: String(raw.status ?? 'SUCCEEDED').toUpperCase(),
    data_snapshot_id:
      optionalString(raw.data_snapshot_id) ?? optionalString(raw.dataset_id),
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
    equity_curve: normalizeCurve(raw.equity_curve, 'equity'),
    drawdown_curve: normalizeCurve(raw.drawdown_curve, 'drawdown'),
    monthly_returns: recordValue(raw.monthly_returns ?? robustness.monthly_returns),
    annual_returns: recordValue(raw.annual_returns ?? robustness.annual_returns),
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

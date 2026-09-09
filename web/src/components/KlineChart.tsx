import type { DatasetBar, SrLevel } from '../types';

interface KlineChartProps {
  candles: DatasetBar[];
  levels?: SrLevel[];
  height?: number;
}

export function KlineChart({ candles, levels = [], height = 420 }: KlineChartProps) {
  if (!candles.length) {
    return <div className="empty">暂无可绘制的K线</div>;
  }

  const width = 960;
  const plotHeight = height - 38;
  const lows = candles.map((bar) => bar.low);
  const highs = candles.map((bar) => bar.high);
  const levelPrices = levels.flatMap((level) => [level.low, level.high, level.center]);
  const rawLow = Math.min(...lows, ...(levelPrices.length ? levelPrices : lows));
  const rawHigh = Math.max(...highs, ...(levelPrices.length ? levelPrices : highs));
  const padding = Math.max((rawHigh - rawLow) * 0.08, rawHigh * 0.001);
  const min = rawLow - padding;
  const max = rawHigh + padding;
  const xStep = width / candles.length;
  const bodyWidth = Math.max(1.5, Math.min(16, xStep * 0.64));

  const xAt = (index: number) => index * xStep + xStep / 2;
  const yAt = (price: number) => plotHeight - ((price - min) / (max - min)) * plotHeight;
  const priceLabel = (price: number) => price.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
  const tickIndexes = [0, Math.floor(candles.length * 0.25), Math.floor(candles.length * 0.5), Math.floor(candles.length * 0.75), candles.length - 1];

  return (
    <div className="kline-chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="K线图">
        {[0.2, 0.4, 0.6, 0.8].map((ratio) => {
          const y = plotHeight * ratio;
          const price = max - (max - min) * ratio;
          return (
            <g key={ratio}>
              <line x1={0} y1={y} x2={width} y2={y} className="kline-grid" />
              <text x={width - 4} y={y - 4} textAnchor="end" className="kline-text">{priceLabel(price)}</text>
            </g>
          );
        })}

        {levels.map((level, index) => {
          const top = yAt(level.high);
          const bottom = yAt(level.low);
          const isSupport = level.zone_type === 'support';
          const label = `${isSupport ? '支撑' : '阻力'} ${priceLabel(level.center)}${level.tfs ? ` · ${level.tfs}` : ''}`;
          return (
            <g key={`${level.zone_type}-${level.center}-${index}`} className="kline-level">
              <rect
                x={0}
                y={Math.min(top, bottom)}
                width={width - 62}
                height={Math.max(2, Math.abs(bottom - top))}
                fill={isSupport ? 'rgba(31,111,94,0.16)' : 'rgba(180,35,24,0.14)'}
                stroke={isSupport ? '#1f6f5e' : '#b42318'}
                strokeWidth={0.8}
              >
                <title>{label}</title>
              </rect>
              <text x={4} y={Math.min(top, bottom) - 4} className={`kline-level-text ${isSupport ? 'support' : 'resistance'}`}>{label}</text>
            </g>
          );
        })}

        {candles.map((bar, index) => {
          const x = xAt(index);
          const bullish = bar.close >= bar.open;
          const bodyTop = yAt(Math.max(bar.open, bar.close));
          const bodyBottom = yAt(Math.min(bar.open, bar.close));
          return (
            <g key={`${bar.session_id}-${index}`} className="kline-candle">
              <line x1={x} y1={yAt(bar.high)} x2={x} y2={yAt(bar.low)} className={bullish ? 'wick bullish' : 'wick bearish'} />
              <rect
                x={x - bodyWidth / 2}
                y={bodyTop}
                width={bodyWidth}
                height={Math.max(1, bodyBottom - bodyTop)}
                className={bullish ? 'body bullish' : 'body bearish'}
              >
                <title>
                  {`${bar.session_id}\n开 ${priceLabel(bar.open)} · 高 ${priceLabel(bar.high)}\n低 ${priceLabel(bar.low)} · 收 ${priceLabel(bar.close)}`}
                </title>
              </rect>
            </g>
          );
        })}

        {tickIndexes.map((index) => (
          <text key={index} x={xAt(index)} y={height - 8} textAnchor={index === 0 ? 'start' : index === candles.length - 1 ? 'end' : 'middle'} className="kline-text">
            {candles[index].session_id}
          </text>
        ))}
      </svg>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { Palette, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import type { DatasetBar, SrLevel } from '../types';

interface KlineChartProps {
  candles: DatasetBar[];
  levels?: SrLevel[];
  height?: number;
}

interface ChartCandle extends CandlestickData<Time> {
  session_id: string;
}

const CHINA_COLORS_STORAGE_KEY = 'xquant.kline.china-colors.v1';
const CHINA_COLORS_EVENT = 'xquant:kline-china-colors';

const INTERNATIONAL_COLORS = {
  up: '#157f6a',
  down: '#c32f22',
};

const CHINA_COLORS = {
  up: '#c62828',
  down: '#16855f',
};

function loadChinaColors(): boolean {
  try {
    return localStorage.getItem(CHINA_COLORS_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function saveChinaColors(enabled: boolean): void {
  try {
    localStorage.setItem(CHINA_COLORS_STORAGE_KEY, String(enabled));
  } catch {
    // The preference still applies for the current session when storage is unavailable.
  }
  window.dispatchEvent(new Event(CHINA_COLORS_EVENT));
}

function useChinaColors() {
  const [enabled, setEnabled] = useState(loadChinaColors);

  useEffect(() => {
    const sync = () => setEnabled(loadChinaColors());
    window.addEventListener('storage', sync);
    window.addEventListener(CHINA_COLORS_EVENT, sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener(CHINA_COLORS_EVENT, sync);
    };
  }, []);

  const update = (value: boolean) => {
    setEnabled(value);
    saveChinaColors(value);
  };

  return [enabled, update] as const;
}

function parseChartTime(sessionId: string, index: number): Time {
  const value = sessionId.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;

  const timestamp = Date.parse(value);
  if (Number.isFinite(timestamp)) return Math.floor(timestamp / 1000) as UTCTimestamp;

  return (Math.floor(Date.UTC(1970, 0, 1) / 1000) + index) as UTCTimestamp;
}

function timeKey(time: Time | undefined): string {
  if (typeof time === 'number') return `timestamp:${time}`;
  if (typeof time === 'string') return `date:${time}`;
  if (time && typeof time === 'object') return `date:${time.year}-${time.month}-${time.day}`;
  return '';
}

function formatSession(sessionId: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(sessionId)) return sessionId;

  const timestamp = Date.parse(sessionId);
  if (!Number.isFinite(timestamp)) return sessionId;
  return new Date(timestamp).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function inferPricePrecision(candles: DatasetBar[]): number {
  const values = candles.flatMap((bar) => [bar.open, bar.high, bar.low, bar.close]);

  for (let precision = 0; precision <= 6; precision += 1) {
    const scale = 10 ** precision;
    if (values.every((value) => Math.abs(value * scale - Math.round(value * scale)) < 1e-7)) {
      return Math.max(2, precision);
    }
  }
  return 6;
}

function formatPrice(value: number, precision: number): string {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

export function KlineChart({ candles, levels = [], height = 420 }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const candleMapRef = useRef<Map<string, ChartCandle>>(new Map());
  const precisionRef = useRef(2);
  const [chinaColors, setChinaColors] = useChinaColors();
  const [hovered, setHovered] = useState<ChartCandle | null>(null);

  const precision = useMemo(() => inferPricePrecision(candles), [candles]);
  const palette = chinaColors ? CHINA_COLORS : INTERNATIONAL_COLORS;
  const chartData = useMemo<ChartCandle[]>(
    () =>
      candles.map((bar, index) => ({
        time: parseChartTime(bar.session_id, index),
        session_id: bar.session_id,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    [candles],
  );

  precisionRef.current = precision;
  candleMapRef.current = new Map(chartData.map((bar) => [timeKey(bar.time), bar]));

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 1),
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#66727f',
        fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif',
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: '#eef1f4' },
        horzLines: { color: '#eef1f4' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#8593a0', width: 1, style: LineStyle.Dashed },
        horzLine: { color: '#8593a0', width: 1, style: LineStyle.Dashed },
      },
      rightPriceScale: {
        borderColor: '#dce3ea',
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#dce3ea',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 2,
        minBarSpacing: 1,
      },
      localization: {
        locale: 'zh-CN',
        priceFormatter: (price: number) => formatPrice(price, precisionRef.current),
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true,
      },
    });

    const series = chart.addCandlestickSeries({
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
      priceLineVisible: true,
      lastValueVisible: true,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (param.time === undefined) {
        setHovered(chartData[chartData.length - 1] ?? null);
        return;
      }
      setHovered(candleMapRef.current.get(timeKey(param.time)) ?? null);
    };
    chart.subscribeCrosshairMove(handleCrosshairMove);

    const resizeObserver = new ResizeObserver(() => {
      const width = containerRef.current?.clientWidth;
      if (width) chart.resize(Math.max(width, 1), height);
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      priceLinesRef.current = [];
    };
  }, [height]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    series.setData(chartData);
    setHovered(chartData[chartData.length - 1] ?? null);
    chartRef.current?.timeScale().fitContent();
  }, [chartData]);

  useEffect(() => {
    seriesRef.current?.applyOptions({
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
    });
  }, [palette.down, palette.up]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    priceLinesRef.current.forEach((line) => series.removePriceLine(line));
    priceLinesRef.current = levels.flatMap((level) => {
      const isSupport = level.zone_type === 'support';
      const color = isSupport ? '#1f6f5e' : '#b42318';
      const label = `${isSupport ? '支撑' : '阻力'} ${formatPrice(level.center, precision)}${level.tfs ? ` · ${level.tfs}` : ''}`;
      const bounds = [level.low, level.high].map((price) =>
        series.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          lineVisible: true,
          axisLabelVisible: false,
          title: '',
        }),
      );
      const center = series.createPriceLine({
        price: level.center,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        lineVisible: true,
        axisLabelVisible: true,
        axisLabelColor: color,
        axisLabelTextColor: '#ffffff',
        title: label,
      });
      return [...bounds, center];
    });
  }, [levels, precision]);

  if (!candles.length) {
    return <div className="empty">暂无可绘制的K线</div>;
  }

  const changeZoom = (factor: number) => {
    const chart = chartRef.current;
    const timeScale = chart?.timeScale();
    const range = timeScale?.getVisibleLogicalRange();
    if (!timeScale || !range) return;

    const center = (range.from + range.to) / 2;
    const span = Math.max(4, (range.to - range.from) * factor);
    timeScale.setVisibleLogicalRange({ from: center - span / 2, to: center + span / 2 });
  };

  const displayBar = hovered ?? chartData[chartData.length - 1] ?? null;
  const isBullish = displayBar ? displayBar.close >= displayBar.open : true;

  return (
    <div className={`kline-chart${chinaColors ? ' china-colors' : ''}`}>
      <div className="kline-toolbar">
        <div className="kline-toolbar-actions" role="group" aria-label="K线视图控制">
          <button
            type="button"
            className="button kline-icon-button"
            onClick={() => changeZoom(0.75)}
            title="放大"
            aria-label="放大"
          >
            <ZoomIn size={15} />
          </button>
          <button
            type="button"
            className="button kline-icon-button"
            onClick={() => changeZoom(1.35)}
            title="缩小"
            aria-label="缩小"
          >
            <ZoomOut size={15} />
          </button>
          <button
            type="button"
            className="button kline-icon-button"
            onClick={() => chartRef.current?.timeScale().fitContent()}
            title="重置视图"
            aria-label="重置视图"
          >
            <RotateCcw size={15} />
          </button>
        </div>
        <div className="kline-toolbar-meta">
          {displayBar ? (
            <div className="kline-readout" aria-live="polite">
              <span className="kline-readout-time">{formatSession(displayBar.session_id)}</span>
              <span>
                开 <strong className={`kline-value ${isBullish ? 'up' : 'down'}`}>{formatPrice(displayBar.open, precision)}</strong>
              </span>
              <span>
                高 <strong className={`kline-value ${isBullish ? 'up' : 'down'}`}>{formatPrice(displayBar.high, precision)}</strong>
              </span>
              <span>
                低 <strong className={`kline-value ${isBullish ? 'up' : 'down'}`}>{formatPrice(displayBar.low, precision)}</strong>
              </span>
              <span>
                收 <strong className={`kline-value ${isBullish ? 'up' : 'down'}`}>{formatPrice(displayBar.close, precision)}</strong>
              </span>
            </div>
          ) : null}
          <label className="checkbox-row kline-locale-toggle" title="切换为红涨绿跌的中国市场配色">
            <input
              type="checkbox"
              checked={chinaColors}
              onChange={(event) => setChinaColors(event.target.checked)}
            />
            <Palette size={14} />
            红涨绿跌
          </label>
        </div>
      </div>
      <div className="kline-canvas" ref={containerRef} style={{ height }} aria-label="K线图" />
    </div>
  );
}

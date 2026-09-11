import type { MouseEventHandler } from 'react';

type ExchangeOption = readonly [code: string, label: string];

interface ExchangeGroup {
  label: string;
  options: readonly ExchangeOption[];
}

const EXCHANGE_GROUPS: readonly ExchangeGroup[] = [
  {
    label: '中国及港澳台',
    options: [
      ['SSE', 'SSE (上海证券交易所)'],
      ['SZSE', 'SZSE (深圳证券交易所)'],
      ['BSE', 'BSE (北京证券交易所)'],
      ['HKEX', 'HKEX (香港交易所)'],
      ['HKFE', 'HKFE (香港期货交易所)'],
      ['TWSE', 'TWSE (台湾证券交易所)'],
      ['TPEX', 'TPEX (台湾柜买中心)'],
      ['TAIFEX', 'TAIFEX (台湾期货交易所)'],
    ],
  },
  {
    label: '亚太',
    options: [
      ['TSE', 'TSE (东京证券交易所)'],
      ['OSE', 'OSE (大阪交易所)'],
      ['JPX', 'JPX (日本交易所集团)'],
      ['KRX', 'KRX (韩国交易所)'],
      ['NSE', 'NSE (印度国家证券交易所)'],
      ['BSE', 'BSE (孟买证券交易所)'],
      ['MCX', 'MCX (印度多种商品交易所)'],
      ['NCDEX', 'NCDEX (印度国家商品交易所)'],
      ['SGX', 'SGX (新加坡交易所)'],
      ['ASX', 'ASX (澳大利亚证券交易所)'],
      ['NZX', 'NZX (新西兰交易所)'],
      ['MYX', 'MYX (马来西亚交易所)'],
      ['IDX', 'IDX (印尼证券交易所)'],
      ['SET', 'SET (泰国证券交易所)'],
      ['TFEX', 'TFEX (泰国期货交易所)'],
      ['HOSE', 'HOSE (胡志明市证券交易所)'],
      ['HNX', 'HNX (河内证券交易所)'],
      ['UPCOM', 'UPCOM (越南 UPCoM)'],
      ['PSE', 'PSE (菲律宾证券交易所)'],
      ['DSEBD', 'DSEBD (达卡证券交易所)'],
      ['CSELK', 'CSELK (科伦坡证券交易所)'],
      ['CSEMA', 'CSEMA (斯里兰卡股票市场)'],
      ['PSX', 'PSX (巴基斯坦证券交易所)'],
    ],
  },
  {
    label: '美洲',
    options: [
      ['NASDAQ', 'NASDAQ'],
      ['NYSE', 'NYSE'],
      ['AMEX', 'AMEX / NYSE Arca'],
      ['BOATS', 'BOATS (美国场外交易)'],
      ['OTC', 'OTC Markets'],
      ['CBOE', 'CBOE'],
      ['CBOEFTSE', 'CBOE FTSE'],
      ['CME', 'CME'],
      ['CME_MINI', 'CME_MINI'],
      ['CBOT', 'CBOT'],
      ['NYMEX', 'NYMEX'],
      ['COMEX', 'COMEX'],
      ['ICEUS', 'ICEUS'],
      ['TSX', 'TSX (多伦多证券交易所)'],
      ['TSXV', 'TSXV (多伦多创业板)'],
      ['NEO', 'NEO Exchange'],
      ['CSE', 'CSE (加拿大证券交易所)'],
      ['BMFBOVESPA', 'BMFBOVESPA (巴西)'],
      ['B3', 'B3 (巴西证券交易所)'],
      ['BMV', 'BMV (墨西哥证券交易所)'],
      ['BYMA', 'BYMA (阿根廷)'],
      ['BCS', 'BCS (智利)'],
      ['BVC', 'BVC (哥伦比亚)'],
      ['BVL', 'BVL (秘鲁)'],
    ],
  },
  {
    label: '欧洲',
    options: [
      ['LSE', 'LSE (伦敦证券交易所)'],
      ['LSIN', 'LSIN (伦敦国际)'],
      ['XETR', 'XETR (德国交易所)'],
      ['FWB', 'FWB (法兰克福)'],
      ['GETTEX', 'GETTEX'],
      ['TRADEGATE', 'TRADEGATE'],
      ['SWB', 'SWB (斯图加特)'],
      ['Euronext Paris', 'Euronext Paris'],
      ['Euronext Amsterdam', 'Euronext Amsterdam'],
      ['Euronext Brussels', 'Euronext Brussels'],
      ['Euronext Lisbon', 'Euronext Lisbon'],
      ['Euronext Oslo', 'Euronext Oslo'],
      ['Euronext Athens', 'Euronext Athens'],
      ['MIL', 'MIL (意大利证券交易所)'],
      ['BME', 'BME (西班牙证券交易所)'],
      ['SIX', 'SIX (瑞士证券交易所)'],
      ['VIE', 'VIE (维也纳证券交易所)'],
      ['GPW', 'GPW (华沙证券交易所)'],
      ['BVB', 'BVB (布加勒斯特证券交易所)'],
      ['PSECZ', 'PSECZ (布拉格证券交易所)'],
      ['OMXSTO', 'OMXSTO (斯德哥尔摩)'],
      ['OMXCOP', 'OMXCOP (哥本哈根)'],
      ['OMXHEX', 'OMXHEX (赫尔辛基)'],
      ['NGM', 'NGM (瑞典)'],
      ['MOEX', 'MOEX (莫斯科交易所)'],
      ['RUS', 'RUS (俄罗斯)'],
      ['BET', 'BET (布加勒斯特指数市场)'],
    ],
  },
  {
    label: '中东及非洲',
    options: [
      ['TADAWUL', 'TADAWUL (沙特证券交易所)'],
      ['DFM', 'DFM (迪拜金融市场)'],
      ['ADX', 'ADX (阿布扎比证券交易所)'],
      ['QSE', 'QSE (卡塔尔证券交易所)'],
      ['BHB', 'BHB (巴林证券交易所)'],
      ['MSM', 'MSM (马斯喀特证券交易所)'],
      ['KSE', 'KSE (科威特证券交易所)'],
      ['TASE', 'TASE (特拉维夫证券交易所)'],
      ['EGX', 'EGX (埃及交易所)'],
      ['JSE', 'JSE (约翰内斯堡证券交易所)'],
      ['NSENG', 'NSENG (尼日利亚交易所)'],
      ['NSEKE', 'NSEKE (内罗毕证券交易所)'],
      ['LSX', 'LSX (莱索托证券交易所)'],
    ],
  },
  {
    label: '外汇与 CFD',
    options: [
      ['OANDA', 'OANDA'],
      ['FX_IDC', 'FX_IDC'],
      ['FXCM', 'FXCM'],
      ['FOREXCOM', 'FOREX.com'],
      ['SAXO', 'Saxo Bank'],
      ['CAPITALCOM', 'Capital.com'],
      ['PEPPERSTONE', 'Pepperstone'],
      ['SKILLING', 'Skilling'],
      ['ICMARKETS', 'IC Markets'],
      ['EASYMARKETS', 'easyMarkets'],
      ['ACTIVTRADES', 'ActivTrades'],
      ['BLACKBULL', 'BlackBull Markets'],
      ['BLUEBERRY', 'Blueberry Markets'],
      ['CMC', 'CMC Markets'],
      ['EIGHTCAP', 'Eightcap'],
      ['FPMARKETS', 'FP Markets'],
      ['FUSION', 'Fusion Markets'],
      ['FXPRO', 'FxPro'],
      ['GBEBROKERS', 'GBE brokers'],
      ['IG', 'IG'],
      ['INTERACTIVEBROKERS', 'Interactive Brokers'],
      ['JFX', 'JFX'],
      ['MATSUI', 'MATSUI'],
      ['THINKMARKETS', 'ThinkMarkets'],
      ['TICKMILL', 'Tickmill'],
      ['TRADENATION', 'Trade Nation'],
      ['VANTAGE', 'Vantage'],
      ['DERIV', 'Deriv'],
      ['TVC', 'TVC (指数、商品与差价合约)'],
    ],
  },
  {
    label: '期货与商品',
    options: [
      ['EUREX', 'EUREX'],
      ['ICE', 'ICE'],
      ['ICEEUR', 'ICEEUR'],
      ['ICEENDEX', 'ICEENDEX'],
      ['LME', 'LME (伦敦金属交易所)'],
      ['SHFE', 'SHFE (上海期货交易所)'],
      ['INE', 'INE (上海国际能源交易中心)'],
      ['DCE', 'DCE (大连商品交易所)'],
      ['CZCE', 'CZCE (郑州商品交易所)'],
      ['CFFEX', 'CFFEX (中国金融期货交易所)'],
      ['GFEX', 'GFEX (广州期货交易所)'],
      ['ASX24', 'ASX 24 (澳大利亚证券交易所衍生品)'],
      ['MATBAROFEX', 'MATBA ROFEX (阿根廷)'],
      ['NSEIX', 'NSE IX (印度国际交易所)'],
      ['SAFEX', 'SAFEX (南非期货交易所)'],
      ['SFE', 'SFE (悉尼期货交易所)'],
      ['TOCOM', 'TOCOM (东京商品交易所)'],
    ],
  },
  {
    label: '加密货币',
    options: [
      ['BINANCE', 'Binance'],
      ['BINANCEUS', 'Binance US'],
      ['BINGX', 'BingX'],
      ['BITSTAMP', 'Bitstamp'],
      ['BITKUB', 'Bitkub'],
      ['BITUNIX', 'Bitunix'],
      ['BITVAVO', 'Bitvavo'],
      ['BLOFIN', 'BloFin'],
      ['COINBASE', 'Coinbase'],
      ['KRAKEN', 'Kraken'],
      ['BITFINEX', 'Bitfinex'],
      ['BYBIT', 'Bybit'],
      ['OKX', 'OKX'],
      ['KUCOIN', 'KuCoin'],
      ['GATEIO', 'Gate.io'],
      ['HTX', 'HTX'],
      ['MEXC', 'MEXC'],
      ['BITGET', 'Bitget'],
      ['COINEX', 'CoinEx'],
      ['DELTA', 'Delta Exchange India'],
      ['HYPERLIQUID', 'Hyperliquid'],
      ['KCEX', 'KCEX'],
      ['LBANK', 'LBank'],
      ['PHEMEX', 'Phemex'],
      ['PIONEX', 'Pionex'],
      ['PYTH', 'Pyth'],
      ['POLONIEX', 'Poloniex'],
      ['UPBIT', 'Upbit'],
      ['BITHUMB', 'Bithumb'],
      ['BITFLYER', 'bitFlyer'],
      ['KORBIT', 'Korbit'],
      ['BITMEX', 'BitMEX'],
      ['DERIBIT', 'Deribit'],
      ['BITSO', 'Bitso'],
      ['CEXIO', 'CEX.IO'],
      ['TOOBIT', 'Toobit'],
      ['WEEX', 'WEEX'],
      ['CRYPTO', 'CRYPTO'],
      ['CRYPTOCAP', 'CRYPTOCAP'],
    ],
  },
  {
    label: '指数与经济数据',
    options: [
      ['SP', 'SP (标普指数)'],
      ['DJ', 'DJ (道琼斯指数)'],
      ['DJCFD', 'DJCFD'],
      ['SPCFD', 'SPCFD'],
      ['FTSE', 'FTSE'],
      ['HSI', 'HSI (恒生指数)'],
      ['CFI', 'CFI'],
      ['USI', 'USI'],
      ['INDEX', 'INDEX'],
      ['ECONOMICS', 'ECONOMICS'],
      ['FRED', 'FRED'],
    ],
  },
];

interface TradingViewExchangeSelectProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
  ariaLabel?: string;
  onClick?: MouseEventHandler<HTMLSelectElement>;
}

export function TradingViewExchangeSelect({
  id,
  value,
  onChange,
  className,
  ariaLabel,
  onClick,
}: TradingViewExchangeSelectProps) {
  const isKnown = EXCHANGE_GROUPS.some((group) =>
    group.options.some(([code]) => code === value),
  );

  return (
    <select
      id={id}
      className={className}
      value={value}
      aria-label={ariaLabel}
      onClick={onClick}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">自动识别</option>
      {value && !isKnown ? <option value={value}>{value} (当前)</option> : null}
      {EXCHANGE_GROUPS.map((group) => (
        <optgroup key={group.label} label={group.label}>
          {group.options.map(([code, label]) => (
            <option key={`${group.label}-${code}`} value={code}>
              {label}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

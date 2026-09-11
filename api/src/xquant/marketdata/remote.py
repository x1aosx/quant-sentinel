from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_YAHOO_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "1d": "1d",
    "1w": "1wk",
}

_EASTMONEY_KLT = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1d": "101",
    "1w": "102",
}

_TENCENT_KLINE_TYPES = {
    "1m": "m1",
    "5m": "m5",
    "15m": "m15",
    "30m": "m30",
    "1h": "m60",
    "1d": "day",
    "1w": "week",
}

_TRADINGVIEW_INTERVALS = {
    "1m": "in_1_minute",
    "5m": "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "1h": "in_1_hour",
    "1d": "in_daily",
    "1w": "in_weekly",
}

_MT5_TIMEFRAMES = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "1d": "TIMEFRAME_D1",
    "1w": "TIMEFRAME_W1",
}

_SUPPORTED_SOURCES = {"yfinance", "akshare", "tradingview", "mt5"}
_SUPPORTED_TIMEFRAMES = frozenset(_YAHOO_INTERVALS)
_MT5_LOCK = threading.Lock()

_REMOTE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RemoteImportRequest:
    source: str = "yfinance"
    symbol: str = "GC=F"
    timeframe: str = "1d"
    lookback: int = 250
    adjust: str = "qfq"
    exchange: str | None = None
    session_start: str | None = None
    session_end: str | None = None


class RemoteSymbolNotFound(ValueError):
    """Raised when a remote provider does not recognize the requested symbol."""


def validate_remote_request(raw: Mapping[str, Any] | RemoteImportRequest) -> RemoteImportRequest:
    if isinstance(raw, RemoteImportRequest):
        request = raw
    else:
        request = RemoteImportRequest(
            source=str(raw.get("source") or "yfinance"),
            symbol=str(raw.get("symbol") or ""),
            timeframe=str(raw.get("timeframe") or "1d"),
            lookback=int(raw.get("lookback") or 250),
            adjust=str(raw.get("adjust") or "qfq"),
            exchange=str(raw.get("exchange") or "") or None,
            session_start=str(raw.get("session_start") or "") or None,
            session_end=str(raw.get("session_end") or "") or None,
        )
    source = str(request.source or "").strip().lower()
    if source not in _SUPPORTED_SOURCES:
        raise ValueError("数据源仅支持 yfinance、akshare、tradingview 或 mt5")
    symbol = str(request.symbol or "").strip()
    if not symbol:
        raise ValueError("symbol 不能为空")
    timeframe = str(request.timeframe or "1d").strip().lower()
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise ValueError("周期仅支持 1m/5m/15m/30m/1h/1d/1w")
    if not 10 <= request.lookback <= 5000:
        raise ValueError("lookback 必须在 10 到 5000 之间")
    adjust = str(request.adjust or "qfq").strip().lower()
    if adjust not in {"qfq", "hfq", "none"}:
        raise ValueError("adjust 仅支持 qfq/hfq/none")
    return RemoteImportRequest(
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        lookback=request.lookback,
        adjust=adjust,
        exchange=(request.exchange or "").strip().upper() or None,
        session_start=request.session_start,
        session_end=request.session_end,
    )


def normalize_remote_payload(raw_bars: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_bars):
        try:
            open_price = float(raw["open"])
            high = float(raw["high"])
            low = float(raw["low"])
            close = float(raw["close"])
            volume = float(raw.get("volume") or 0.0)
            session_id = str(raw.get("session_id") or raw.get("time") or raw.get("date") or "").strip()
            closed = bool(raw.get("closed", True))
        except (KeyError, TypeError, ValueError):
            continue
        if not session_id or session_id in seen:
            continue
        if min(open_price, high, low, close) <= 0 or not all(
            math.isfinite(value) for value in (open_price, high, low, close)
        ):
            continue
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            continue
        if not math.isfinite(volume):
            volume = 0.0
        if closed is False:
            continue
        seen.add(session_id)
        bars.append(
            {
                "session_id": session_id,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(volume, 0.0),
            }
        )
    bars.sort(key=lambda item: item["session_id"])
    return bars


def _iso_timestamp(value: Any) -> str:
    try:
        timestamp = int(value)
        if abs(timestamp) >= 1_000_000_000_000_000:
            timestamp /= 1_000_000
        elif abs(timestamp) >= 1_000_000_000_000:
            timestamp /= 1_000
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("行情时间戳无效") from exc


def _yahoo_range(request: RemoteImportRequest) -> str:
    if request.timeframe == "1m":
        return "7d"
    if request.timeframe in {"5m", "15m", "30m", "1h"}:
        return "60d"
    if request.timeframe == "1w":
        return "5y"
    return "2y"


def _http_get(
    url: str,
    *,
    params: dict[str, Any],
    referer: str,
    attempts: int = 3,
) -> httpx.Response:
    headers = {**_REMOTE_HEADERS, "Referer": referer}
    last_error: Exception | None = None
    response: httpx.Response | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=15.0,
                follow_redirects=True,
            )
        except httpx.TransportError as exc:
            last_error = exc
        else:
            if response.status_code not in _RETRY_STATUS_CODES:
                return response
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
        if attempt < attempts - 1:
            time.sleep(min(2.0**attempt, 4.0))

    if last_error:
        raise last_error
    if response is None:
        raise ValueError("行情请求未获得响应")
    response.raise_for_status()
    return response


def _parse_session_date(value: str, field_name: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    try:
        if len(text) == 8 and text.isdigit():
            parsed = datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
        else:
            parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 ISO-8601 或 YYYYMMDD 日期") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _yahoo_symbol_candidates(symbol: str) -> list[str]:
    text = symbol.strip().upper()
    candidates: list[str] = []

    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", text):
        suffix = {"SH": ".SS", "SZ": ".SZ", "BJ": ".BJ"}[text[:2]]
        candidates.append(f"{text[2:]}{suffix}")
    elif text.isdigit() and len(text) == 6:
        if text.startswith(("4", "8")):
            candidates.append(f"{text}.BJ")
        elif text.startswith(("0", "1", "2", "3")):
            candidates.append(f"{text}.SZ")
        else:
            candidates.append(f"{text}.SS")

    if text not in candidates:
        candidates.append(text)
    return candidates


def _is_numeric_market_symbol(symbol: str) -> bool:
    return bool(re.fullmatch(r"(?:SH|SZ|BJ)?\d{5,6}", symbol.strip().upper()))


def _fetch_yahoo(request: RemoteImportRequest) -> list[dict[str, Any]]:
    params = {
        "interval": _YAHOO_INTERVALS[request.timeframe],
        "includePrePost": "false",
    }
    if request.session_start:
        params["period1"] = str(
            int(_parse_session_date(request.session_start, "session_start").timestamp())
        )
        end = (
            _parse_session_date(request.session_end, "session_end")
            if request.session_end
            else datetime.now(UTC)
        )
        params["period2"] = str(int(end.timestamp()))
    else:
        params["range"] = _yahoo_range(request)

    candidates = _yahoo_symbol_candidates(request.symbol)
    for candidate in candidates:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(candidate, safe='')}"
        response = _http_get(
            url,
            params=params,
            referer="https://finance.yahoo.com/",
        )
        if response.status_code in {400, 404}:
            continue
        response.raise_for_status()
        payload = response.json()
        result = payload.get("chart", {}).get("result") or []
        if not result:
            continue
        bars = result[0]
        timestamps = bars.get("timestamp") or []
        quote_data = bars.get("indicators", {}).get("quote", [{}])[0]
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            try:
                row = {
                    "session_id": _iso_timestamp(timestamp),
                    "open": quote_data["open"][index],
                    "high": quote_data["high"][index],
                    "low": quote_data["low"][index],
                    "close": quote_data["close"][index],
                    "volume": quote_data["volume"][index],
                    "closed": index < len(timestamps) - 1,
                }
            except (KeyError, IndexError, TypeError):
                continue
            rows.append(row)
        normalized = normalize_remote_payload(rows)[-request.lookback :]
        if normalized:
            return normalized

    tried = "、".join(candidates)
    raise RemoteSymbolNotFound(
        f"YFinance 未找到 symbol「{request.symbol}」（已尝试 {tried}）。"
        "Yahoo Finance 不保证收录新三板、北交所或券商内部代码；"
        "请改用 tradingview、mt5 或 akshare，并确认行情代码。"
    )


def _datetime_session_id(value: Any) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
        return parsed.isoformat(timespec="seconds")
    return _iso_timestamp(value)


def _infer_tradingview_exchange(symbol: str) -> str:
    text = symbol.strip().upper()
    if not (text.isdigit() and len(text) == 6):
        return ""
    if text.startswith(("4", "8")):
        return "BSE"
    if text.startswith(("6", "5", "9")):
        return "SSE"
    return "SZSE"


def _tradingview_exchange_candidates(request: RemoteImportRequest) -> list[str]:
    exchange = (request.exchange or "").strip().upper()
    if exchange and exchange != "AUTO":
        return [exchange]
    inferred = _infer_tradingview_exchange(request.symbol)
    return [inferred] if inferred else [""]


def _close_tradingview_socket(client: Any) -> None:
    socket = getattr(client, "ws", None)
    if socket is None:
        return
    try:
        socket.close()
    except Exception as exc:  # noqa: BLE001 - third-party socket close is best effort
        logger.debug("TradingView socket close failed: %s", exc)
    finally:
        try:
            client.ws = None
        except Exception as exc:  # noqa: BLE001 - third-party client cleanup is best effort
            logger.debug("TradingView socket reset failed: %s", exc)


def _fetch_tradingview(request: RemoteImportRequest) -> tuple[list[dict[str, Any]], str]:
    try:
        from tvDatafeed import Interval, TvDatafeed
    except ImportError as exc:
        raise ValueError(
            "TradingView 数据源依赖未安装，请执行 "
            "pip install git+https://github.com/rongardF/tvdatafeed.git"
        ) from exc

    interval_name = _TRADINGVIEW_INTERVALS[request.timeframe]
    try:
        interval = getattr(Interval, interval_name)
    except AttributeError as exc:
        raise ValueError(f"TradingView 不支持周期：{request.timeframe}") from exc

    last_error: Exception | None = None
    tried: list[str] = []
    for exchange in _tradingview_exchange_candidates(request):
        label = f"{exchange or 'AUTO'}:{request.symbol}"
        tried.append(label)
        client: Any | None = None
        try:
            client = TvDatafeed()
            try:
                client._TvDatafeed__ws_timeout = 10.0
            except Exception as exc:  # noqa: BLE001 - private optional setting
                logger.debug("TradingView timeout override failed: %s", exc)
            frame = client.get_hist(
                symbol=request.symbol,
                exchange=exchange,
                interval=interval,
                n_bars=min(request.lookback + 1, 5001),
            )
        except Exception as exc:  # noqa: BLE001 - tvDatafeed raises library-specific errors
            last_error = exc
            continue
        finally:
            if client is not None:
                _close_tradingview_socket(client)

        if frame is None or getattr(frame, "empty", True):
            continue

        frame = frame.reset_index()
        columns = {str(column).lower(): column for column in frame.columns}
        timestamp_column = columns.get("datetime") or columns.get("date") or frame.columns[0]
        rows: list[dict[str, Any]] = []
        for index, row in frame.iterrows():
            try:
                rows.append(
                    {
                        "session_id": _datetime_session_id(row[timestamp_column]),
                        "open": row[columns["open"]],
                        "high": row[columns["high"]],
                        "low": row[columns["low"]],
                        "close": row[columns["close"]],
                        "volume": row[columns.get("volume", frame.columns[0])]
                        if "volume" in columns
                        else 0.0,
                        "closed": index < len(frame) - 1,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        bars = normalize_remote_payload(rows)[-request.lookback :]
        if bars:
            return bars, exchange

    detail = f"；最后错误：{last_error}" if last_error else ""
    raise ValueError(
        f"TradingView 未返回有效行情数据（{request.symbol}，已尝试 {', '.join(tried)}）{detail}"
    )


def _fetch_mt5(request: RemoteImportRequest) -> list[dict[str, Any]]:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise ValueError(
            "MT5 数据源仅在 Windows 可用，请安装 MetaTrader5 并保持终端已登录"
        ) from exc

    timeframe_name = _MT5_TIMEFRAMES[request.timeframe]
    try:
        timeframe = getattr(mt5, timeframe_name)
    except AttributeError as exc:
        raise ValueError(f"MT5 不支持周期：{request.timeframe}") from exc

    with _MT5_LOCK:
        if not mt5.initialize():
            raise ValueError(f"MT5 初始化失败：{mt5.last_error()}，请确认终端已启动并登录")
        try:
            symbol_info = mt5.symbol_info(request.symbol)
            if symbol_info is None:
                raise ValueError(f"MT5 品种不存在：{request.symbol}")
            if not mt5.symbol_select(request.symbol, True):
                raise ValueError(f"MT5 无法订阅品种：{request.symbol}")
            rates = mt5.copy_rates_from_pos(
                request.symbol,
                timeframe,
                0,
                min(request.lookback + 1, 5001),
            )
            if rates is None or len(rates) == 0:
                raise ValueError(
                    f"MT5 未返回行情数据：{request.symbol} {request.timeframe}，"
                    f"错误：{mt5.last_error()}"
                )

            rows: list[dict[str, Any]] = []
            for index, rate in enumerate(rates):
                try:
                    rows.append(
                        {
                            "session_id": _iso_timestamp(rate["time"]),
                            "open": rate["open"],
                            "high": rate["high"],
                            "low": rate["low"],
                            "close": rate["close"],
                            "volume": rate["tick_volume"],
                            "closed": index < len(rates) - 1,
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            return normalize_remote_payload(rows)[-request.lookback :]
        finally:
            mt5.shutdown()


def _eastmoney_code(symbol: str) -> tuple[str, str]:
    text = symbol.strip().upper()
    if text.startswith("SH") and text[2:].isdigit():
        return "1", text[2:]
    if text.startswith("SZ") and text[2:].isdigit():
        return "0", text[2:]
    if text.startswith("BJ") and text[2:].isdigit():
        return "2", text[2:]
    if text.isdigit() and len(text) == 6:
        if text.startswith(("4", "8")):
            return "2", text
        market = "1" if text.startswith(("6", "9", "5")) else "0"
        return market, text
    raise ValueError("akshare/A股 品种请使用 600519、SH600519、SZ000001 或 BJ800865")


def _fetch_eastmoney(request: RemoteImportRequest) -> list[dict[str, Any]]:
    market, code = _eastmoney_code(request.symbol)
    begin_default = (datetime.now(UTC) - timedelta(days=1000)).strftime("%Y%m%d")
    end_default = datetime.now(UTC).strftime("%Y%m%d")
    begin = request.session_start or begin_default
    end = request.session_end or end_default
    if request.session_start:
        begin = _parse_session_date(request.session_start, "session_start").strftime("%Y%m%d")
    if request.session_end:
        end = _parse_session_date(request.session_end, "session_end").strftime("%Y%m%d")
    response = _http_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": f"{market}.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "klt": _EASTMONEY_KLT[request.timeframe],
            "fqt": {"qfq": "1", "hfq": "2", "none": "0"}[request.adjust],
            "beg": begin,
            "end": end,
            "lmt": str(max(request.lookback, 100)),
        },
        referer="https://quote.eastmoney.com/",
        attempts=1,
    )
    response.raise_for_status()
    klines = response.json().get("data", {}).get("klines") or []
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(klines):
        cells = str(line).split(",")
        if len(cells) < 6:
            continue
        try:
            row = {
                "session_id": cells[0],
                "open": cells[1],
                "high": cells[2],
                "low": cells[3],
                "close": cells[4],
                "volume": cells[5],
                "closed": index < len(klines) - 1,
            }
        except (ValueError, TypeError):
            continue
        rows.append(row)
    return normalize_remote_payload(rows)[-request.lookback :]


def _fetch_tencent(request: RemoteImportRequest) -> list[dict[str, Any]]:
    market, code = _eastmoney_code(request.symbol)
    prefix = {"0": "sz", "1": "sh", "2": "bj"}[market]
    tencent_symbol = f"{prefix}{code}"
    period = _TENCENT_KLINE_TYPES[request.timeframe]
    count = min(max(request.lookback + 1, 10), 5000)
    adjustment = {"qfq": "qfq", "hfq": "hfq", "none": ""}[request.adjust]

    if request.timeframe in {"1d", "1w"}:
        begin = (
            _parse_session_date(request.session_start, "session_start").strftime("%Y-%m-%d")
            if request.session_start
            else ""
        )
        end = (
            _parse_session_date(request.session_end, "session_end").strftime("%Y-%m-%d")
            if request.session_end
            else ""
        )
        param = f"{tencent_symbol},{period},{begin},{end},{count},{adjustment}"
        response = _http_get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": param},
            referer="https://gu.qq.com/",
        )
        series_key = f"{adjustment}{period}" if adjustment else period
    else:
        response = _http_get(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            params={"param": f"{tencent_symbol},{period},,{count}"},
            referer="https://gu.qq.com/",
        )
        series_key = period

    response.raise_for_status()
    section = (response.json().get("data") or {}).get(tencent_symbol) or {}
    raw_rows = section.get(series_key) or []
    rows: list[dict[str, Any]] = []
    for index, cells in enumerate(raw_rows):
        if not isinstance(cells, Sequence) or len(cells) < 6:
            continue
        try:
            rows.append(
                {
                    "session_id": str(cells[0]),
                    "open": cells[1],
                    "high": cells[3],
                    "low": cells[4],
                    "close": cells[2],
                    "volume": cells[5],
                    "closed": index < len(raw_rows) - 1,
                }
            )
        except (TypeError, ValueError):
            continue
    return normalize_remote_payload(rows)[-request.lookback :]


def _fetch_akshare(request: RemoteImportRequest) -> tuple[list[dict[str, Any]], str]:
    eastmoney_error: Exception | None = None
    try:
        bars = _fetch_eastmoney(request)
        if bars:
            return bars, "eastmoney_public_kline"
        eastmoney_error = ValueError("未返回有效 K 线")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        eastmoney_error = exc

    try:
        bars = _fetch_tencent(request)
        if bars:
            return bars, "tencent_public_kline"
        tencent_error = ValueError("未返回有效 K 线")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        tencent_error = exc

    raise ValueError(
        f"A股行情获取失败：东方财富接口不可用（{eastmoney_error}）；"
        f"腾讯接口不可用（{tencent_error}）"
    ) from tencent_error


def fetch_remote_bars(request: RemoteImportRequest | Mapping[str, Any]) -> dict[str, Any]:
    request = validate_remote_request(request)
    exchange = request.exchange or ""
    fallback_from: str | None = None
    if request.source == "yfinance":
        try:
            bars = _fetch_yahoo(request)
        except RemoteSymbolNotFound as exc:
            if not _is_numeric_market_symbol(request.symbol):
                raise
            try:
                bars, exchange = _fetch_tradingview(request)
            except (ImportError, ValueError) as fallback_exc:
                raise ValueError(f"{exc}；TradingView 回退失败：{fallback_exc}") from fallback_exc
            provider = "tradingview_tvdatafeed"
            source = "tradingview"
            fallback_from = "yfinance"
        else:
            provider = "yfinance_public_chart"
            source = "yfinance"
    elif request.source == "akshare":
        bars, provider = _fetch_akshare(request)
        source = "akshare"
        if provider == "tencent_public_kline":
            fallback_from = "eastmoney"
    elif request.source == "tradingview":
        bars, exchange = _fetch_tradingview(request)
        provider = "tradingview_tvdatafeed"
        source = "tradingview"
    else:
        bars = _fetch_mt5(request)
        provider = "mt5_terminal"
        source = "mt5"
    if not bars:
        raise ValueError(f"{provider} 未返回有效行情数据")
    result = {
        "symbol": request.symbol.upper(),
        "timeframe": request.timeframe,
        "source": source,
        "source_provider": provider,
        "exchange": exchange or None,
        "simulation_only": True,
        "bars": bars,
    }
    if fallback_from:
        result["fallback_from"] = fallback_from
    return result

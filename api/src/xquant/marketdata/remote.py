from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

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


@dataclass(frozen=True)
class RemoteImportRequest:
    source: str = "yfinance"
    symbol: str = "GC=F"
    timeframe: str = "1d"
    lookback: int = 250
    adjust: str = "qfq"
    session_start: str | None = None
    session_end: str | None = None


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
            session_start=str(raw.get("session_start") or "") or None,
            session_end=str(raw.get("session_end") or "") or None,
        )
    if request.source not in {"yfinance", "akshare"}:
        raise ValueError("数据源仅支持 yfinance 或 akshare")
    if not request.symbol.strip():
        raise ValueError("symbol 不能为空")
    if request.timeframe not in _YAHOO_INTERVALS:
        raise ValueError("周期仅支持 1m/5m/15m/30m/1h/1d/1w")
    if not 10 <= request.lookback <= 5000:
        raise ValueError("lookback 必须在 10 到 5000 之间")
    if request.adjust not in {"qfq", "hfq", "none"}:
        raise ValueError("adjust 仅支持 qfq/hfq/none")
    return request


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
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index + 1} 条行情数据无效") from exc
        if not session_id or session_id in seen:
            continue
        if min(open_price, high, low, close) <= 0 or not all(
            math.isfinite(value) for value in (open_price, high, low, close)
        ):
            continue
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            continue
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


def _iso_ms(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat(timespec="seconds")
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


def _fetch_yahoo(request: RemoteImportRequest) -> list[dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(request.symbol, safe='')}"
    response = httpx.get(
        url,
        params={
            "interval": _YAHOO_INTERVALS[request.timeframe],
            "range": _yahoo_range(request),
            "includePrePost": "false",
        },
        timeout=15.0,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise ValueError("YFinance 未返回行情数据")
    bars = result[0]
    timestamps = bars.get("timestamp") or []
    quote_data = bars.get("indicators", {}).get("quote", [{}])[0]
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        try:
            row = {
                "session_id": _iso_ms(timestamp),
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
    return normalize_remote_payload(rows)[-request.lookback :]


def _eastmoney_code(symbol: str) -> tuple[str, str]:
    text = symbol.strip().upper()
    if text.startswith("SH") and text[2:].isdigit():
        return "1", text[2:]
    if text.startswith("SZ") and text[2:].isdigit():
        return "0", text[2:]
    if text.isdigit() and len(text) == 6:
        market = "1" if text.startswith(("6", "9", "5")) else "0"
        return market, text
    raise ValueError("akshare/A股 品种请使用 600519、SH600519 或 SZ000001")


def _fetch_akshare(request: RemoteImportRequest) -> list[dict[str, Any]]:
    market, code = _eastmoney_code(request.symbol)
    begin = request.session_start or (datetime.now(UTC) - timedelta(days=1000)).strftime("%Y%m%d")
    end = request.session_end or datetime.now(UTC).strftime("%Y%m%d")
    response = httpx.get(
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
        timeout=15.0,
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


def fetch_remote_bars(request: RemoteImportRequest | Mapping[str, Any]) -> dict[str, Any]:
    request = validate_remote_request(request)
    if request.source == "yfinance":
        bars = _fetch_yahoo(request)
        provider = "yfinance_public_chart"
    else:
        bars = _fetch_akshare(request)
        provider = "eastmoney_public_kline"
    return {
        "symbol": request.symbol.upper(),
        "timeframe": request.timeframe,
        "source": request.source,
        "source_provider": provider,
        "simulation_only": True,
        "bars": bars,
    }

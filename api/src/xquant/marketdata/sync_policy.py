"""数据中心在线行情「自动同步」的策略常量与参数组装。

自动同步的归属粒度是**数据集**（symbol + timeframe），而不是标的：
每个数据集对应一条 interval 调度计划，默认频率与周期对齐（15m 周期默认 15 分钟），
这样新增的周期不会被已有计划的参数快照漏掉。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

DATASET_SYNC_ORIGIN = "data-center.dataset-sync"
DATASET_SYNC_TASK = "market.daily.sync"
SYNC_TIMEZONE = "Asia/Shanghai"

MIN_INTERVAL_SECONDS = 60
FALLBACK_INTERVAL_SECONDS = 300
DEFAULT_LOOKBACK = 500
DEFAULT_ADJUST = "qfq"
DEFAULT_MAX_CATCH_UP_RUNS = 30

REMOTE_SOURCES = frozenset({"yfinance", "akshare", "tradingview", "mt5"})

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "1d": 86_400,
    "1w": 604_800,
}


def is_remote_source(source: Any) -> bool:
    """本地文件数据集不能远程同步。"""

    return str(source or "").strip().lower() in REMOTE_SOURCES


def timeframe_seconds(timeframe: Any) -> int | None:
    return TIMEFRAME_SECONDS.get(str(timeframe or "").strip().lower())


def min_interval_seconds(timeframe: Any) -> int:
    """同步间隔不得密于周期本身，否则只是空跑抓取。"""

    seconds = timeframe_seconds(timeframe)
    return max(MIN_INTERVAL_SECONDS, seconds or FALLBACK_INTERVAL_SECONDS)


def default_interval_seconds(timeframe: Any) -> int:
    """新数据集的默认同步频率：跟随周期。"""

    return min_interval_seconds(timeframe)


def clamp_interval_seconds(timeframe: Any, interval_seconds: Any) -> int:
    """把历史计划里可能过密的间隔抬到当前策略下限（迁移用，不抛错）。"""

    minimum = min_interval_seconds(timeframe)
    try:
        value = int(float(interval_seconds))
    except (TypeError, ValueError):
        return minimum
    return max(minimum, value)


def normalize_interval_seconds(timeframe: Any, interval_seconds: Any) -> int:
    """校验用户设置的同步间隔，违反周期下限时抛 ValueError。"""

    minimum = min_interval_seconds(timeframe)
    if interval_seconds is None:
        return minimum
    try:
        value = int(float(interval_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("同步频率必须是秒数") from exc
    if value < minimum:
        raise ValueError(
            f"{str(timeframe or '').strip() or '该'} 周期的同步间隔不能小于 {minimum} 秒"
        )
    return value


def dataset_sync_schedule_id(symbol: Any, timeframe: Any) -> str:
    """稳定的 dataset 级计划 id，重复启用不会产生重复计划。"""

    key = f"{str(symbol or '').strip().upper()}|{str(timeframe or '').strip().lower()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:40]
    return f"dataset-sync-{slug or 'dataset'}-{digest}"


def params_origin(params: Any) -> str:
    if not isinstance(params, Mapping):
        return ""
    return str(params.get("origin") or "")


def params_dataset_id(params: Any) -> str | None:
    if not isinstance(params, Mapping):
        return None
    value = str(params.get("dataset_id") or "").strip()
    return value or None


def is_dataset_sync_params(params: Any) -> bool:
    return params_origin(params) == DATASET_SYNC_ORIGIN and params_dataset_id(params) is not None


def is_legacy_sync_params(params: Any) -> bool:
    """旧版按 symbol 建立的计划：一个计划塞了整组周期。"""

    if params_origin(params) != DATASET_SYNC_ORIGIN:
        return False
    return params_dataset_id(params) is None and isinstance(params.get("symbols"), Sequence)


def legacy_entries(params: Any) -> list[dict[str, Any]]:
    if not isinstance(params, Mapping):
        return []
    raw = params.get("symbols")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def build_dataset_sync_params(
    dataset: Mapping[str, Any],
    *,
    lookback: Any = DEFAULT_LOOKBACK,
    adjust: Any = DEFAULT_ADJUST,
) -> dict[str, Any]:
    """构造 market.daily.sync 的入参：一条计划只覆盖一个数据集。"""

    params: dict[str, Any] = {
        "origin": DATASET_SYNC_ORIGIN,
        "dataset_id": str(dataset["id"]),
        "symbol": str(dataset.get("symbol") or ""),
        "timeframe": str(dataset.get("timeframe") or "").strip().lower(),
        "source": str(dataset.get("source") or ""),
        "lookback": int(lookback or DEFAULT_LOOKBACK),
        "adjust": str(adjust or DEFAULT_ADJUST),
    }
    exchange = str(dataset.get("exchange") or "").strip()
    if exchange:
        params["exchange"] = exchange
    return params


__all__ = [
    "DATASET_SYNC_ORIGIN",
    "DATASET_SYNC_TASK",
    "DEFAULT_ADJUST",
    "DEFAULT_LOOKBACK",
    "DEFAULT_MAX_CATCH_UP_RUNS",
    "FALLBACK_INTERVAL_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "REMOTE_SOURCES",
    "SYNC_TIMEZONE",
    "TIMEFRAME_SECONDS",
    "build_dataset_sync_params",
    "clamp_interval_seconds",
    "dataset_sync_schedule_id",
    "default_interval_seconds",
    "is_dataset_sync_params",
    "is_legacy_sync_params",
    "is_remote_source",
    "legacy_entries",
    "min_interval_seconds",
    "normalize_interval_seconds",
    "params_dataset_id",
    "params_origin",
    "timeframe_seconds",
]

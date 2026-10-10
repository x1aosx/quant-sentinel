"""行情存储丢 K 线后的批量重建。

InfluxDB 3 的 ``market_bar`` 表随写入自动创建，一旦实例的数据目录被重建或清空，
表就会消失，而 PostgreSQL ``research.dataset`` 里的元数据仍在（``bar_count``
不为 0）。此时读数据集会得到 ``table 'public.iox.market_bar' not found``，
AI 分析因为没有 K 线而无法进行。

``repair_dataset_bars`` 逐个数据集回源重建：按元数据里的 ``bar_count`` 放大
``lookback``（``sync_dataset`` 会自动改成全量请求），并把单个数据集的失败收集
到报告里，不影响其它数据集。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

# 回源时可用的数据源；``local`` 之类本地上传的数据集没有远端可回源。
_REMOTE_SOURCES = frozenset({"yfinance", "akshare", "tradingview", "mt5"})

# 数据集的 bar_count 缺失或过小时，至少按这个数量回源。
DEFAULT_LOOKBACK_FLOOR = 250

# 远端接口允许的 lookback 区间（见 marketdata.remote.validate_remote_request）。
_MIN_LOOKBACK = 10
_MAX_LOOKBACK = 5000


def repair_dataset_bars(
    database: Any,
    *,
    dataset_ids: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    lookback_floor: int = DEFAULT_LOOKBACK_FLOOR,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按数据集回源重建行情存储中的 K 线。

    ``database`` 只需提供 ``list_datasets()`` 与 ``sync_dataset(payload)``。
    返回的报告里每个数据集带 ``status``：``repaired`` / ``partial`` /
    ``planned`` / ``skipped`` / ``failed``。``partial`` 表示回源成功但拿回的 K 线
    少于元数据里的 ``bar_count``（公开接口的分钟级历史上限很低），需要人工确认。
    """
    wanted_ids = {str(item).strip() for item in dataset_ids or [] if str(item).strip()}
    wanted_symbols = {str(item).strip().upper() for item in symbols or [] if str(item).strip()}
    datasets = [
        dataset
        for dataset in database.list_datasets()
        if (not wanted_ids or str(dataset.get("id")) in wanted_ids)
        and (
            not wanted_symbols or str(dataset.get("symbol") or "").strip().upper() in wanted_symbols
        )
    ]

    items: list[dict[str, Any]] = []
    for dataset in datasets:
        payload = _sync_payload(dataset, lookback_floor)
        item = {
            "id": str(dataset.get("id") or ""),
            "symbol": str(dataset.get("symbol") or ""),
            "timeframe": str(dataset.get("timeframe") or ""),
            "stored_bar_count": int(dataset.get("bar_count") or 0),
        }
        if payload is None:
            items.append(
                {
                    **item,
                    "status": "skipped",
                    "reason": f"数据源 {dataset.get('source')} 不支持回源重建",
                }
            )
            continue
        if dry_run:
            items.append({**item, "status": "planned", "payload": payload})
            continue
        try:
            result = database.sync_dataset(payload)
        except Exception as exc:  # noqa: BLE001 - 单个数据集失败不应中断整体重建
            logger.warning("重建数据集 %s 的 K 线失败：%s", item["id"], exc)
            items.append({**item, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        restored = int(result.get("total_count") or 0)
        store = {"bar_count": restored, "rebuilt": bool(result.get("rebuilt"))}
        if 0 < restored < item["stored_bar_count"]:
            # 回源成功但比原数据集短：公开接口的分钟级历史往往只有几百根。
            items.append({**item, **store, "status": "partial"})
            continue
        items.append({**item, **store, "status": "repaired"})

    failed = sum(1 for item in items if item["status"] == "failed")
    repaired = sum(1 for item in items if item["status"] in {"repaired", "partial"})
    if failed and not repaired:
        status = "failed"
    elif failed or any(item["status"] == "partial" for item in items):
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "total": len(items),
        "repaired": sum(1 for item in items if item["status"] == "repaired"),
        "partial": sum(1 for item in items if item["status"] == "partial"),
        "planned": sum(1 for item in items if item["status"] == "planned"),
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
        "failed": failed,
        "items": items,
    }


def _sync_payload(
    dataset: Mapping[str, Any],
    lookback_floor: int,
) -> dict[str, Any] | None:
    source = str(dataset.get("source") or "").strip().lower()
    symbol = str(dataset.get("symbol") or "").strip()
    if source not in _REMOTE_SOURCES or not symbol:
        return None
    lookback = min(
        _MAX_LOOKBACK,
        max(int(dataset.get("bar_count") or 0), max(_MIN_LOOKBACK, int(lookback_floor))),
    )
    payload: dict[str, Any] = {"symbol": symbol, "source": source, "lookback": lookback}
    timeframe = str(dataset.get("timeframe") or "").strip()
    if timeframe:
        payload["timeframe"] = timeframe
    return payload


__all__ = ["DEFAULT_LOOKBACK_FLOOR", "repair_dataset_bars"]

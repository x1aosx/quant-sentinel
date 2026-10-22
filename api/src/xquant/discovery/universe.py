"""Candidate-universe expansion for the discovery module.

The discovery engine only ranks the stock snapshots it is given.  Until a
symbol is persisted as a dataset it never reaches that engine, so a pure
cross-sectional ranking can never look outside the local dataset list.  This
module closes that gap: it gathers candidate leads from market intelligence and
from public market rankings, scores them, and (optionally) syncs the best ones
into the local dataset store so the regular discovery run can rank them.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean
from typing import Any, Protocol, runtime_checkable

import httpx

from .domain import (
    EventSnapshot,
    Serializable,
    ThemeSnapshot,
    utc_now,
)

logger = logging.getLogger(__name__)

UNIVERSE_MODEL_VERSION = "discovery-universe-v1"

ORIGIN_INTELLIGENCE = "intelligence"
ORIGIN_MARKET_RANKING = "market_ranking"

STATUS_SYNCED = "SYNCED"
STATUS_PENDING = "PENDING"
STATUS_REJECTED = "REJECTED"
STATUS_EXISTING = "EXISTING"

_DEFAULT_TIMEFRAME = "1d"
_DEFAULT_LOOKBACK = 250

# Only tradable Shanghai/Shenzhen A-share codes: main board, ChiNext, STAR.
_TRADABLE_A_SHARE_RE = re.compile(r"[036]\d{5}")
_SYMBOL_SUFFIX_RE = re.compile(r"^(?:(?:SH|SZ|BJ)|(?:SH|SZ|BJ)\.)?(\d{6})(?:\.(?:SS|SZ|BJ))?$")

# Names carrying these markers are suspended, in delisting review, or too new
# to have a usable history: they must not enter the research universe.
_EXCLUDED_NAME_MARKERS = ("ST", "退")
_EXCLUDED_NAME_PREFIXES = ("N", "C")

_EASTMONEY_RANKING_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_EASTMONEY_A_SHARE_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_EASTMONEY_FIELDS = "f2,f3,f5,f6,f8,f9,f10,f12,f14,f20,f21,f62"
_EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://quote.eastmoney.com/",
}
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

_SINA_RANKING_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_SINA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def normalize_a_share_symbol(value: Any) -> str | None:
    """Return a bare 6-digit tradable A-share code, or ``None``.

    Accepts ``600519``, ``SH600519``, ``600519.SS`` and similar spellings that
    market intelligence and market-data providers mix freely.
    """

    text = str(value or "").strip().upper()
    if not text:
        return None
    match = _SYMBOL_SUFFIX_RE.match(text)
    if match is None:
        return None
    code = match.group(1)
    return code if _TRADABLE_A_SHARE_RE.fullmatch(code) else None


def is_excluded_name(name: str) -> bool:
    """Reject ST/delisting/newly listed names that must not seed the universe."""

    text = str(name or "").strip().upper()
    if not text:
        return False
    if any(marker in text for marker in _EXCLUDED_NAME_MARKERS):
        return True
    return text.startswith(_EXCLUDED_NAME_PREFIXES)


@dataclass(slots=True)
class UniverseLead:
    """One raw candidate sighting before scoring and deduplication."""

    symbol: str
    name: str = ""
    origin: str = ORIGIN_INTELLIGENCE
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class UniverseCandidate(Serializable):
    """One scored candidate with its outcome and audit trail."""

    symbol: str
    name: str
    origin: str
    score: float
    status: str
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)
    dataset_id: str | None = None
    note: str = ""
    discovered_at: datetime | None = None


@dataclass(slots=True)
class UniverseScanReport(Serializable):
    """Result of one universe expansion run."""

    scanned_at: datetime
    model_version: str = UNIVERSE_MODEL_VERSION
    dataset_count: int = 0
    lead_count: int = 0
    candidate_count: int = 0
    synced_count: int = 0
    origin_counts: dict[str, int] = field(default_factory=dict)
    candidates: list[UniverseCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class UniverseLeadSource(Protocol):
    name: str

    def discover(self) -> Sequence[UniverseLead]: ...


@runtime_checkable
class UniverseIntelSource(Protocol):
    """The intelligence snapshots the discovery data source already exposes."""

    def list_event_snapshots(self) -> Sequence[EventSnapshot]: ...

    def list_theme_snapshots(self) -> Sequence[ThemeSnapshot]: ...


HttpJson = Callable[[str, Mapping[str, Any], Mapping[str, str]], Any]


def fetch_json(
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    attempts: int = 2,
    timeout: float = 12.0,
) -> Any:
    """GET a JSON document with a bounded retry budget."""

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = httpx.get(
                url,
                params=dict(params),
                headers=dict(headers),
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.TransportError as exc:
            last_error = exc
        else:
            if response.status_code not in _RETRY_STATUS_CODES:
                response.raise_for_status()
                return response.json()
            last_error = httpx.HTTPStatusError(
                f"榜单接口返回 {response.status_code}",
                request=response.request,
                response=response,
            )
        if attempt < attempts - 1:
            time.sleep(min(2.0**attempt, 3.0))
    if last_error is not None:
        raise last_error
    raise ValueError("榜单接口未获得响应")


@dataclass(frozen=True, slots=True)
class RankingBoard:
    """One public market ranking used as an external lead source."""

    key: str
    label: str
    sort_field: str
    weight: float = 1.0


DEFAULT_RANKING_BOARDS: tuple[RankingBoard, ...] = (
    RankingBoard("change", "涨幅榜", "f3", 0.40),
    RankingBoard("capital", "主力净流入榜", "f62", 0.35),
    RankingBoard("amount", "成交额榜", "f6", 0.25),
)


class EastmoneyRankingSource:
    """Public Eastmoney rankings as an external quality-lead source.

    A failure here is a degraded source, never a failed scan: the caller keeps
    the intelligence leads and records a warning.
    """

    name = "eastmoney_ranking"

    def __init__(
        self,
        *,
        boards: Sequence[RankingBoard] = DEFAULT_RANKING_BOARDS,
        size: int = 20,
        min_amount: float = 5e7,
        http_json: HttpJson | None = None,
    ) -> None:
        self.boards = tuple(boards)
        self.size = max(1, min(int(size), 100))
        self.min_amount = max(0.0, float(min_amount))
        self._http_json = http_json or fetch_json

    def discover(self) -> Sequence[UniverseLead]:
        leads: list[UniverseLead] = []
        for board in self.boards:
            leads.extend(self._board_leads(board))
        return leads

    def _board_leads(self, board: RankingBoard) -> list[UniverseLead]:
        payload = self._http_json(
            _EASTMONEY_RANKING_URL,
            {
                "pn": "1",
                "pz": str(self.size),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": board.sort_field,
                "fs": _EASTMONEY_A_SHARE_FILTER,
                "fields": _EASTMONEY_FIELDS,
            },
            _EASTMONEY_HEADERS,
        )
        if not isinstance(payload, Mapping):
            raise TypeError("东财榜单接口返回的 JSON 不是对象")
        rows = ((payload.get("data") or {}) if isinstance(payload, Mapping) else {}).get(
            "diff"
        ) or []
        leads: list[UniverseLead] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            symbol = normalize_a_share_symbol(row.get("f12"))
            if symbol is None:
                continue
            name = str(row.get("f14") or "").strip()
            if is_excluded_name(name):
                continue
            amount = _number(row.get("f6"))
            if amount is not None and amount < self.min_amount:
                continue
            rank = index + 1
            leads.append(
                UniverseLead(
                    symbol=symbol,
                    name=name,
                    origin=ORIGIN_MARKET_RANKING,
                    reasons=[f"{board.label}第 {rank} 名"],
                    signals={
                        f"{board.key}_rank": float(rank),
                        "rank_score": _ranking_score(rank, self.size),
                        "change_pct": _number(row.get("f3")) or 0.0,
                        "amount": amount or 0.0,
                        "net_inflow": _number(row.get("f62")) or 0.0,
                        "turnover_rate": _number(row.get("f8")) or 0.0,
                        "pe": _number(row.get("f9")) or 0.0,
                        "market_cap": _number(row.get("f20")) or 0.0,
                    },
                )
            )
        return leads


def _ranking_score(rank: int, size: int) -> float:
    span = max(size - 1, 1)
    return clamp(100.0 - (rank - 1) / span * 45.0, 55.0, 100.0)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


DEFAULT_SINA_BOARDS: tuple[RankingBoard, ...] = (
    RankingBoard("change", "涨幅榜", "changepercent", 0.40),
    RankingBoard("amount", "成交额榜", "amount", 0.30),
    RankingBoard("turnover", "换手率榜", "turnoverratio", 0.30),
)


class SinaRankingSource:
    """Public Sina rankings — the fallback when Eastmoney is unreachable.

    Sina exposes no main-capital-inflow board, so this source contributes
    change/amount/turnover leaders and leaves capital signals to Eastmoney.
    """

    name = "sina_ranking"

    def __init__(
        self,
        *,
        boards: Sequence[RankingBoard] = DEFAULT_SINA_BOARDS,
        size: int = 20,
        min_amount: float = 5e7,
        http_json: HttpJson | None = None,
    ) -> None:
        self.boards = tuple(boards)
        self.size = max(1, min(int(size), 100))
        self.min_amount = max(0.0, float(min_amount))
        self._http_json = http_json or fetch_json

    def discover(self) -> Sequence[UniverseLead]:
        leads: list[UniverseLead] = []
        for board in self.boards:
            leads.extend(self._board_leads(board))
        return leads

    def _board_leads(self, board: RankingBoard) -> list[UniverseLead]:
        payload = self._http_json(
            _SINA_RANKING_URL,
            {
                "page": "1",
                "num": str(self.size),
                "sort": board.sort_field,
                "asc": "0",
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "page",
            },
            _SINA_HEADERS,
        )
        if isinstance(payload, (str, bytes)) or not isinstance(payload, Sequence):
            raise TypeError("新浪榜单接口返回的 JSON 不是数组")
        leads: list[UniverseLead] = []
        for index, row in enumerate(payload):
            if not isinstance(row, Mapping):
                continue
            symbol = normalize_a_share_symbol(row.get("code") or row.get("symbol"))
            if symbol is None:
                continue
            name = str(row.get("name") or "").strip()
            if is_excluded_name(name):
                continue
            amount = _number(row.get("amount"))
            if amount is not None and amount < self.min_amount:
                continue
            rank = index + 1
            leads.append(
                UniverseLead(
                    symbol=symbol,
                    name=name,
                    origin=ORIGIN_MARKET_RANKING,
                    reasons=[f"{board.label}第 {rank} 名"],
                    signals={
                        f"{board.key}_rank": float(rank),
                        "rank_score": _ranking_score(rank, self.size),
                        "change_pct": _number(row.get("changepercent")) or 0.0,
                        "amount": amount or 0.0,
                        "turnover_rate": _number(row.get("turnoverratio")) or 0.0,
                        "pe": _number(row.get("per")) or 0.0,
                        "market_cap": (_number(row.get("mktcap")) or 0.0) * 1e4,
                    },
                )
            )
        return leads


def intelligence_leads(
    events: Sequence[EventSnapshot],
    themes: Sequence[ThemeSnapshot],
) -> list[UniverseLead]:
    """Turn intelligence events into candidate leads.

    A stock mentioned by a positive, important event in a hot theme is a lead
    worth checking; one only mentioned by negative events is dropped here and
    left to the regular risk handling.
    """

    theme_heat = {theme.theme_id: float(theme.current_heat_score) for theme in themes}
    theme_names: dict[str, str] = {theme.theme_id: str(theme.name) for theme in themes}
    per_symbol: dict[str, dict[str, Any]] = {}

    for event in events:
        for raw_symbol in event.affected_stock_ids:
            symbol = normalize_a_share_symbol(raw_symbol)
            if symbol is None:
                continue
            entry = per_symbol.setdefault(
                symbol,
                {"events": [], "themes": set(), "risks": []},
            )
            entry["events"].append(event)
            for theme_id in event.affected_theme_ids:
                entry["themes"].add(str(theme_id))

    leads: list[UniverseLead] = []
    for symbol, entry in per_symbol.items():
        events_for_symbol: list[EventSnapshot] = entry["events"]
        event_scores = [_event_lead_score(event) for event in events_for_symbol]
        event_component = mean(event_scores)
        theme_ids = sorted(entry["themes"])
        heats = [theme_heat[theme_id] for theme_id in theme_ids if theme_id in theme_heat]
        theme_component = 50.0 + (mean(heats) - 50.0) * 0.5 if heats else 50.0
        score = clamp(event_component * 0.6 + theme_component * 0.4, 0.0, 100.0)
        if score < 50.0:
            continue

        reasons = [
            f"情报提及 {len(events_for_symbol)} 条事件",
            f"事件影响分 {event_component:.0f}",
        ]
        if heats:
            labels: list[str] = [theme_names.get(item) or item for item in theme_ids[:3]]
            reasons.append("关联主题：" + "、".join(labels))
        directions = {str(event.direction).upper() for event in events_for_symbol}
        if "NEGATIVE" in directions:
            reasons.append("存在负面事件，需人工复核")
        leads.append(
            UniverseLead(
                symbol=symbol,
                name="",
                origin=ORIGIN_INTELLIGENCE,
                reasons=reasons,
                signals={
                    "intelligence_score": score,
                    "event_count": float(len(events_for_symbol)),
                    "theme_heat": mean(heats) if heats else 50.0,
                },
            )
        )
    return leads


def _event_lead_score(event: EventSnapshot) -> float:
    strength = float(event.importance) * float(event.magnitude) / 100.0
    direction = str(event.direction).upper()
    if direction == "POSITIVE":
        return clamp(50.0 + strength * 0.4, 0.0, 100.0)
    if direction == "NEGATIVE":
        return clamp(50.0 - strength * 0.5, 0.0, 100.0)
    if direction == "MIXED":
        return clamp(50.0 + strength * 0.1, 0.0, 100.0)
    return clamp(50.0 + strength * 0.15, 0.0, 100.0)


@dataclass(slots=True)
class _Aggregate:
    symbol: str
    name: str = ""
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)
    intel_score: float = 0.0
    ranking_score: float = 0.0
    origins: set[str] = field(default_factory=set)


def _merge_leads(leads: Sequence[UniverseLead]) -> list[_Aggregate]:
    merged: dict[str, _Aggregate] = {}
    for lead in leads:
        symbol = normalize_a_share_symbol(lead.symbol)
        if symbol is None:
            continue
        aggregate = merged.setdefault(symbol, _Aggregate(symbol=symbol))
        if lead.name and not aggregate.name:
            aggregate.name = lead.name
        aggregate.origins.add(lead.origin)
        for reason in lead.reasons:
            if reason not in aggregate.reasons:
                aggregate.reasons.append(reason)
        for key, value in lead.signals.items():
            number = _number(value)
            if number is None:
                continue
            if key.endswith("_rank") or key == "rank_score":
                aggregate.signals[key] = number
            else:
                aggregate.signals.setdefault(key, number)
        if lead.origin == ORIGIN_INTELLIGENCE:
            aggregate.intel_score = max(
                aggregate.intel_score, float(lead.signals.get("intelligence_score") or 0.0)
            )
        else:
            aggregate.ranking_score = max(
                aggregate.ranking_score, float(lead.signals.get("rank_score") or 0.0)
            )
    return list(merged.values())


def score_aggregate(aggregate: _Aggregate) -> float:
    """Combine independent sources; corroboration is worth a small bonus."""

    components = [
        score for score in (aggregate.intel_score, aggregate.ranking_score) if score > 0.0
    ]
    if not components:
        return 0.0
    score = max(components)
    if len(components) > 1:
        score += 6.0
    return clamp(score, 0.0, 100.0)


SyncCallable = Callable[[str], Mapping[str, Any]]


def dataset_sync_callable(database: Any) -> SyncCallable:
    """Build the standard A-share dataset sync used by universe expansion."""

    def sync(symbol: str) -> Mapping[str, Any]:
        return database.sync_dataset(
            {
                "source": "akshare",
                "symbol": symbol,
                "timeframe": _DEFAULT_TIMEFRAME,
                "lookback": _DEFAULT_LOOKBACK,
                "adjust": "qfq",
            }
        )

    return sync


class UniverseScanner:
    """Collect external leads, score them, and pull the best into datasets."""

    def __init__(
        self,
        intel_source: UniverseIntelSource,
        database: Any,
        *,
        sources: Sequence[UniverseLeadSource] = (),
        sync: SyncCallable | None = None,
        max_candidates: int = 30,
        max_sync: int = 10,
        min_score: float = 55.0,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.intel_source = intel_source
        self.database = database
        self.sources = tuple(sources)
        self._sync = sync or dataset_sync_callable(database)
        self.max_candidates = max(1, int(max_candidates))
        self.max_sync = max(0, int(max_sync))
        self.min_score = clamp(min_score, 0.0, 100.0)
        self._now = now

    def scan(self, *, sync: bool = True) -> UniverseScanReport:
        existing = self._existing_symbols()
        warnings: list[str] = []
        leads: list[UniverseLead] = []

        try:
            events = list(self.intel_source.list_event_snapshots())
            themes = list(self.intel_source.list_theme_snapshots())
        except Exception as exc:
            logger.warning("情报快照读取失败", exc_info=True)
            warnings.append(f"情报快照读取失败：{exc}")
        else:
            leads.extend(intelligence_leads(events, themes))

        for source in self.sources:
            try:
                leads.extend(source.discover())
            except Exception as exc:
                logger.warning("候选池来源 %s 采集失败：%s", source.name, exc)
                logger.debug("候选池来源 %s 异常详情", source.name, exc_info=True)
                warnings.append(f"{source.name} 采集失败：{exc}")

        aggregates = _merge_leads(leads)
        scored: list[tuple[float, _Aggregate]] = []
        for aggregate in aggregates:
            score = score_aggregate(aggregate)
            if score < self.min_score:
                continue
            if is_excluded_name(aggregate.name):
                continue
            scored.append((score, aggregate))
        scored.sort(key=lambda item: (-item[0], item[1].symbol))
        selected = scored[: self.max_candidates]

        scanned_at = self._now()
        candidates: list[UniverseCandidate] = []
        synced = 0
        for score, aggregate in selected:
            if aggregate.symbol in existing:
                candidates.append(
                    UniverseCandidate(
                        symbol=aggregate.symbol,
                        name=aggregate.name or aggregate.symbol,
                        origin=_origin_label(aggregate.origins),
                        score=round(score, 2),
                        status=STATUS_EXISTING,
                        reasons=list(aggregate.reasons),
                        signals=aggregate.signals,
                        note="已在本地数据集中",
                        discovered_at=scanned_at,
                    )
                )
                continue

            if not sync or synced >= self.max_sync:
                candidates.append(
                    UniverseCandidate(
                        symbol=aggregate.symbol,
                        name=aggregate.name or aggregate.symbol,
                        origin=_origin_label(aggregate.origins),
                        score=round(score, 2),
                        status=STATUS_PENDING,
                        reasons=list(aggregate.reasons),
                        signals=aggregate.signals,
                        note="候选待入库" if sync else "同步已关闭",
                        discovered_at=scanned_at,
                    )
                )
                continue

            try:
                result = self._sync(aggregate.symbol)
            except Exception as exc:
                logger.warning("候选标的 %s 行情同步失败：%s", aggregate.symbol, exc)
                logger.debug("候选标的 %s 同步异常详情", aggregate.symbol, exc_info=True)
                candidates.append(
                    UniverseCandidate(
                        symbol=aggregate.symbol,
                        name=aggregate.name or aggregate.symbol,
                        origin=_origin_label(aggregate.origins),
                        score=round(score, 2),
                        status=STATUS_REJECTED,
                        reasons=list(aggregate.reasons),
                        signals=aggregate.signals,
                        note=f"行情同步失败：{exc}",
                        discovered_at=scanned_at,
                    )
                )
                warnings.append(f"{aggregate.symbol} 行情同步失败：{exc}")
                continue

            synced += 1
            existing.add(aggregate.symbol)
            title = str(result.get("title") or "").strip() if isinstance(result, Mapping) else ""
            candidates.append(
                UniverseCandidate(
                    symbol=aggregate.symbol,
                    name=title or aggregate.name or aggregate.symbol,
                    origin=_origin_label(aggregate.origins),
                    score=round(score, 2),
                    status=STATUS_SYNCED,
                    reasons=list(aggregate.reasons),
                    signals=aggregate.signals,
                    dataset_id=_dataset_id(result),
                    note="已同步行情并纳入候选池",
                    discovered_at=scanned_at,
                )
            )

        origin_counts: dict[str, int] = {}
        for candidate in candidates:
            origin_counts[candidate.origin] = origin_counts.get(candidate.origin, 0) + 1

        return UniverseScanReport(
            scanned_at=scanned_at,
            dataset_count=len(existing),
            lead_count=len(leads),
            candidate_count=len(candidates),
            synced_count=synced,
            origin_counts=origin_counts,
            candidates=candidates,
            warnings=warnings,
        )

    def _existing_symbols(self) -> set[str]:
        symbols: set[str] = set()
        for summary in list(self.database.list_datasets()):
            raw = summary.get("symbol") if isinstance(summary, Mapping) else summary
            symbol = normalize_a_share_symbol(raw)
            if symbol is not None:
                symbols.add(symbol)
        return symbols


def _origin_label(origins: set[str]) -> str:
    if len(origins) > 1:
        return "+".join(sorted(origins))
    return next(iter(origins), ORIGIN_INTELLIGENCE)


def _dataset_id(result: Any) -> str | None:
    if not isinstance(result, Mapping):
        return None
    for key in ("dataset_id", "id"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    return None

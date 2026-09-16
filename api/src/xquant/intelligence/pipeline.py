from __future__ import annotations

import hashlib
import html
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

from .domain import (
    EventImpact,
    EventType,
    ImpactDirection,
    ImpactHorizon,
    InformationStatus,
    MarketEvent,
    RawInformation,
    Theme,
    ThemeCategory,
    ThemeState,
    compute_content_hash,
    ensure_utc,
    information_id,
    utc_now,
)

_BLOCK_TAGS = {
    "article",
    "blockquote",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "section",
    "td",
    "th",
    "tr",
}
_IGNORED_TAGS = {"script", "style", "noscript", "template"}
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "spm",
}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "will",
    "with",
}
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_STOCK_CODE_RE = re.compile(r"(?<!\d)([0368]\d{5})(?!\d)")


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    fuzzy_similarity_threshold: float = 0.82
    cluster_similarity_threshold: float = 0.62
    cluster_window_hours: int = 72
    title_weight: float = 0.65
    content_weight: float = 0.35

    def __post_init__(self) -> None:
        for name in (
            "fuzzy_similarity_threshold",
            "cluster_similarity_threshold",
            "title_weight",
            "content_weight",
        ):
            value = float(getattr(self, name))
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if abs(self.title_weight + self.content_weight) <= 0:
            raise ValueError("at least one similarity weight must be positive")
        if self.cluster_window_hours < 0:
            raise ValueError("cluster_window_hours cannot be negative")


@dataclass(slots=True)
class DeduplicationResult:
    unique: list[RawInformation]
    duplicates: list[RawInformation]

    def to_dict(self) -> dict[str, int]:
        return {"unique": len(self.unique), "duplicates": len(self.duplicates)}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in _IGNORED_TAGS:
            self._ignored_depth += 1
        elif normalized in _BLOCK_TAGS and not self._ignored_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif normalized in _BLOCK_TAGS and not self._ignored_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def clean_html(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (TypeError, ValueError):
        return normalize_text(html.unescape(str(value or "")))
    return normalize_text(html.unescape(parser.text()))


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def canonicalize_url(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    parts = urlsplit(str(value).strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not scheme or not hostname:
        return str(value).strip()
    port = parts.port
    netloc = hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
        and not key.lower().startswith("utm_")
    ]
    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def normalize_information(
    information: RawInformation,
    *,
    now: datetime | None = None,
) -> RawInformation:
    current = ensure_utc(now, field_name="now") or utc_now()
    fetch_time = information.fetch_time
    process_time = max(current, fetch_time)
    publish_time = min(information.publish_time, process_time)
    event_time = (
        min(information.event_time, process_time)
        if information.event_time is not None
        else None
    )
    title = clean_html(information.title)
    content = clean_html(information.content)
    content_hash = information.content_hash or compute_content_hash(title, content)
    return RawInformation(
        id=information.id if information.content_hash else information_id(content_hash),
        source=information.source,
        source_type=information.source_type,
        url=canonicalize_url(information.url),
        title=title,
        content=content,
        author=information.author,
        event_time=event_time,
        publish_time=publish_time,
        fetch_time=fetch_time,
        process_time=process_time,
        available_time=process_time,
        language=information.language,
        raw_payload=dict(information.raw_payload),
        content_hash=content_hash,
        status=InformationStatus.NORMALIZED,
    )


def normalize_information_batch(
    items: Sequence[RawInformation],
    *,
    now: datetime | None = None,
) -> list[RawInformation]:
    return [normalize_information(item, now=now) for item in items]


def exact_deduplicate(items: Sequence[RawInformation]) -> DeduplicationResult:
    unique: list[RawInformation] = []
    duplicates: list[RawInformation] = []
    seen: set[str] = set()
    for item in items:
        keys: list[str] = []
        if item.content_hash:
            keys.append(f"content:{item.content_hash}")
        canonical_url = canonicalize_url(item.url)
        if canonical_url:
            keys.append(f"url:{canonical_url}")
        normalized_title = normalize_text(item.title).lower()
        if len(normalized_title) >= 8:
            title_hash = hashlib.sha256(normalized_title.encode("utf-8")).hexdigest()
            keys.append(f"title:{title_hash}")
        if any(key in seen for key in keys):
            duplicates.append(item)
            continue
        unique.append(item)
        seen.update(keys)
    return DeduplicationResult(unique=unique, duplicates=duplicates)


def tokenize(value: str) -> set[str]:
    normalized = normalize_text(value).lower()
    tokens = {
        token
        for token in _ASCII_TOKEN_RE.findall(normalized)
        if token not in _STOP_WORDS
    }
    for sequence in _CJK_RE.findall(normalized):
        if len(sequence) == 1:
            tokens.add(sequence)
            continue
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def jaccard_similarity(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0


def information_similarity(
    left: RawInformation,
    right: RawInformation,
    *,
    config: PipelineConfig | None = None,
) -> float:
    effective = config or PipelineConfig()
    title_score = jaccard_similarity(left.title, right.title)
    content_score = jaccard_similarity(left.content, right.content)
    if not left.content or not right.content:
        content_score = title_score
    weighted = (
        title_score * effective.title_weight
        + content_score * effective.content_weight
    ) / (effective.title_weight + effective.content_weight)
    if weighted >= 0.35:
        sequence_score = SequenceMatcher(
            None,
            normalize_text(left.title).lower(),
            normalize_text(right.title).lower(),
        ).ratio()
        weighted = max(weighted, sequence_score * 0.9)
    return max(0.0, min(1.0, weighted))


def fuzzy_deduplicate(
    items: Sequence[RawInformation],
    *,
    threshold: float | None = None,
    config: PipelineConfig | None = None,
) -> DeduplicationResult:
    effective = config or PipelineConfig()
    similarity_threshold = (
        effective.fuzzy_similarity_threshold if threshold is None else float(threshold)
    )
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    unique: list[RawInformation] = []
    duplicates: list[RawInformation] = []
    for item in items:
        if any(
            information_similarity(item, existing, config=effective)
            >= similarity_threshold
            for existing in unique
        ):
            duplicates.append(item)
        else:
            unique.append(item)
    return DeduplicationResult(unique=unique, duplicates=duplicates)


def cluster_information(
    items: Sequence[RawInformation],
    *,
    config: PipelineConfig | None = None,
) -> list[list[RawInformation]]:
    effective = config or PipelineConfig()
    ordered = sorted(items, key=lambda item: (item.publish_time, str(item.id)))
    clusters: list[list[RawInformation]] = []
    for item in ordered:
        matched: list[RawInformation] | None = None
        matched_score = -1.0
        for cluster in clusters:
            representative = cluster[0]
            gap = abs(item.publish_time - representative.publish_time)
            if gap > timedelta(hours=effective.cluster_window_hours):
                continue
            score = max(
                information_similarity(item, candidate, config=effective)
                for candidate in cluster
            )
            if score >= effective.cluster_similarity_threshold and score > matched_score:
                matched = cluster
                matched_score = score
        if matched is None:
            clusters.append([item])
        else:
            matched.append(item)
    return clusters


@dataclass(frozen=True, slots=True)
class RuleAnalyzerConfig:
    event_keywords: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "BLACK_SWAN": ("黑天鹅", "系统性风险", "金融危机"),
            "RISK": ("风险", "违约", "暴雷", "处罚", "违规", "退市", "制裁", "暴跌"),
            "MONETARY_POLICY": (
                "降息",
                "加息",
                "降准",
                "利率",
                "货币政策",
                "美联储",
                "央行",
            ),
            "REGULATION": ("监管", "审查", "反垄断", "合规", "牌照", "立案"),
            "POLICY": (
                "政策",
                "规划",
                "国务院",
                "发改委",
                "工信部",
                "财政部",
                "商务部",
                "证监会",
                "补贴",
                "关税",
            ),
            "MACRO": ("GDP", "CPI", "PPI", "PMI", "社融", "通胀", "就业", "经济数据"),
            "GEOPOLITICAL": ("地缘", "冲突", "战争", "停火", "海外", "国际局势"),
            "EARNINGS": ("财报", "业绩", "预告", "净利润", "营收", "超预期"),
            "MERGER": ("并购", "重组", "收购", "合并"),
            "ORDER": ("中标", "订单", "合同", "采购"),
            "PRODUCT": ("新产品", "发布", "量产", "获批", "临床试验"),
            "PRICE_CHANGE": ("涨价", "降价", "提价", "价格上调", "价格下调"),
            "COMMODITY": ("原油", "黄金", "铜", "锂", "煤炭", "天然气"),
            "TECHNOLOGY": (
                "AI",
                "人工智能",
                "大模型",
                "机器人",
                "芯片",
                "半导体",
                "算力",
                "光模块",
                "数据中心",
            ),
            "COMPANY": ("公告", "回购", "增持", "减持", "解禁", "公司"),
            "INDUSTRY": ("行业", "产业", "供需", "产能", "产业链", "原材料"),
            "MARKET": ("指数", "股市", "成交额", "板块", "涨跌停"),
        }
    )
    theme_keywords: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "AI算力": ("AI", "人工智能", "大模型", "算力", "服务器", "光模块", "数据中心"),
            "机器人": ("机器人", "人形机器人", "减速器", "伺服"),
            "半导体": ("芯片", "半导体", "晶圆", "先进制程"),
            "新能源": ("光伏", "风电", "储能", "锂电", "新能源"),
            "创新药": ("创新药", "生物医药", "临床试验", "医药"),
            "黄金": ("黄金", "金价", "避险"),
            "原油": ("原油", "油价", "石油"),
            "航运": ("航运", "运价", "港口"),
            "消费": ("消费", "零售", "白酒", "家电"),
            "军工": ("军工", "国防", "航空航天"),
        }
    )
    industry_keywords: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "计算机": ("AI", "人工智能", "算力", "数据中心"),
            "电子": ("芯片", "半导体", "光模块", "消费电子"),
            "电力设备": ("光伏", "风电", "储能", "锂电"),
            "医药生物": ("创新药", "生物医药", "临床试验"),
            "有色金属": ("黄金", "铜", "锂"),
            "石油石化": ("原油", "石油", "天然气"),
            "交通运输": ("航运", "运价", "港口"),
            "国防军工": ("军工", "国防", "航空航天"),
        }
    )
    stock_aliases: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "宁德时代": ("宁德时代", "CATL", "300750"),
            "中际旭创": ("中际旭创", "300308"),
            "工业富联": ("工业富联", "601138"),
            "科大讯飞": ("科大讯飞", "002230"),
            "中芯国际": ("中芯国际", "688981"),
        }
    )
    positive_keywords: tuple[str, ...] = (
        "支持",
        "利好",
        "增长",
        "突破",
        "超预期",
        "中标",
        "回购",
        "增持",
        "降息",
        "降准",
        "补贴",
        "涨价",
        "获批",
        "创新高",
    )
    negative_keywords: tuple[str, ...] = (
        "利空",
        "下滑",
        "不及预期",
        "处罚",
        "违规",
        "减持",
        "暴跌",
        "违约",
        "退市",
        "风险",
        "冲突",
        "制裁",
        "加息",
    )
    capital_keywords: tuple[str, ...] = (
        "资金",
        "成交额",
        "北向",
        "南向",
        "融资",
        "ETF",
        "增持",
        "回购",
    )
    product_keywords: tuple[str, ...] = (
        "大模型",
        "机器人",
        "芯片",
        "光模块",
        "储能",
        "创新药",
    )


class RuleAnalyzer:
    def __init__(self, config: RuleAnalyzerConfig | None = None) -> None:
        self.config = config or RuleAnalyzerConfig()

    def analyze(self, items: Sequence[RawInformation]) -> MarketEvent:
        if not items:
            raise ValueError("cannot analyze an empty event cluster")
        representative = max(
            items,
            key=lambda item: (
                len(item.content),
                len(item.title),
                item.publish_time,
            ),
        )
        text = "\n".join(
            f"{item.title}\n{item.content}" for item in items
        )
        event_type, matched = self._event_type(text)
        themes = self._matched_values(text, self.config.theme_keywords)
        industries = self._matched_values(text, self.config.industry_keywords)
        companies, stocks = self._stocks(text)
        products = self._matched_terms(text, self.config.product_keywords)
        risks = self._matched_terms(text, self.config.negative_keywords)
        positive_hits = self._matched_terms(text, self.config.positive_keywords)
        negative_hits = self._matched_terms(text, self.config.negative_keywords)
        direction, sentiment = self._direction(positive_hits, negative_hits)
        source_count = len({(item.source, item.url) for item in items})
        confidence = _bounded(
            0.42 + min(len(matched), 5) * 0.07 + min(source_count, 5) * 0.04,
            0.35,
            0.96,
        )
        novelty = _bounded(0.92 - max(0, source_count - 1) * 0.07, 0.25, 1.0)
        magnitude = _bounded(
            25 + len(positive_hits + negative_hits) * 7 + min(source_count, 5) * 5,
            0,
            100,
        )
        capital_score = min(
            100.0,
            len(self._matched_terms(text, self.config.capital_keywords)) * 18.0
            + len(stocks) * 4.0,
        )
        horizon = self._horizon(event_type)
        priced_in = 0.55 if event_type in {EventType.MARKET, EventType.PRICE_CHANGE} else 0.25
        impact = EventImpact(
            direction=direction,
            magnitude=magnitude,
            confidence=confidence,
            novelty=novelty,
            priced_in=priced_in,
            horizon=horizon,
            affected_themes=themes,
            affected_industries=industries,
            affected_products=products,
            affected_companies=companies,
            affected_stocks=stocks,
            risks=risks,
            capital_score=capital_score,
        )
        publish_times = [item.publish_time for item in items]
        event_times = [item.event_time for item in items if item.event_time is not None]
        event_time = min(event_times) if event_times else min(publish_times)
        importance = _bounded(
            _event_type_weight(event_type)
            + magnitude * 0.35
            + min(source_count, 5) * 3,
            0,
            100,
        )
        heat_score = _bounded(
            35 + importance * 0.4 + min(source_count, 5) * 4 + novelty * 8,
            0,
            100,
        )
        title = representative.title or representative.content[:80]
        summary = _summary(representative.content or representative.title)
        stable_key = f"{normalize_text(title).lower()}|{event_time.date().isoformat()}"
        return MarketEvent(
            id=uuid5(NAMESPACE_URL, f"https://xquant.local/intelligence/event/{stable_key}"),
            event_type=event_type,
            title=title,
            summary=summary,
            country="CN" if _contains_cjk(text) else None,
            importance=importance,
            sentiment=sentiment,
            confidence=confidence,
            novelty=novelty,
            impact_direction=direction,
            impact_horizon=horizon,
            event_time=event_time,
            first_publish_time=min(publish_times),
            last_update_time=max(publish_times),
            heat_score=heat_score,
            source_count=source_count,
            information_ids=[item.id for item in items],
            impact=impact,
        )

    def analyze_clusters(
        self,
        clusters: Sequence[Sequence[RawInformation]],
    ) -> list[MarketEvent]:
        return [self.analyze(cluster) for cluster in clusters if cluster]

    def _event_type(self, text: str) -> tuple[EventType, list[str]]:
        best_type = EventType.OTHER
        best_matches: list[str] = []
        best_score = -1
        for name, keywords in self.config.event_keywords.items():
            matched = self._matched_terms(text, keywords)
            score = len(matched) * 10 + _event_type_priority(EventType(name))
            if matched and score > best_score:
                best_type = EventType(name)
                best_matches = matched
                best_score = score
        return best_type, best_matches

    def _matched_values(
        self,
        text: str,
        mapping: Mapping[str, tuple[str, ...]],
    ) -> list[str]:
        return [
            name
            for name, keywords in mapping.items()
            if self._matched_terms(text, keywords)
        ]

    @staticmethod
    def _matched_terms(text: str, terms: Sequence[str]) -> list[str]:
        lowered = text.lower()
        return [
            term
            for term in terms
            if term.lower() in lowered
        ]

    def _stocks(self, text: str) -> tuple[list[str], list[str]]:
        companies: list[str] = []
        stocks: list[str] = []
        for company, aliases in self.config.stock_aliases.items():
            if any(alias.lower() in text.lower() for alias in aliases):
                companies.append(company)
                stocks.extend(
                    alias
                    for alias in aliases
                    if re.fullmatch(r"[0368]\d{5}", alias)
                )
        stocks.extend(_STOCK_CODE_RE.findall(text))
        return _unique(companies), _unique(stocks)

    @staticmethod
    def _direction(
        positive_hits: Sequence[str],
        negative_hits: Sequence[str],
    ) -> tuple[ImpactDirection, float]:
        positive = len(positive_hits)
        negative = len(negative_hits)
        total = positive + negative
        if not total:
            return ImpactDirection.NEUTRAL, 0.0
        sentiment = _bounded((positive - negative) / total, -1.0, 1.0)
        if positive and negative:
            return ImpactDirection.MIXED, sentiment
        if positive:
            return ImpactDirection.POSITIVE, sentiment
        return ImpactDirection.NEGATIVE, sentiment

    @staticmethod
    def _horizon(event_type: EventType) -> ImpactHorizon:
        if event_type in {
            EventType.RISK,
            EventType.BLACK_SWAN,
            EventType.PRICE_CHANGE,
            EventType.MARKET,
        }:
            return ImpactHorizon.INTRADAY
        if event_type in {
            EventType.POLICY,
            EventType.MONETARY_POLICY,
            EventType.MACRO,
            EventType.GEOPOLITICAL,
        }:
            return ImpactHorizon.LONG
        return ImpactHorizon.MEDIUM


class ThemeHeatUpdater:
    def __init__(self, *, model_version: str = "rules-v1") -> None:
        self.model_version = model_version

    def update(
        self,
        events: Sequence[MarketEvent],
        *,
        existing: Sequence[Theme] | None = None,
        now: datetime | None = None,
    ) -> list[Theme]:
        current_time = ensure_utc(now, field_name="now") or utc_now()
        existing_by_name = {
            theme.name: theme
            for theme in (existing or [])
        }
        grouped: dict[str, list[MarketEvent]] = defaultdict(list)
        for event in events:
            for theme_name in event.impact.affected_themes:
                grouped[theme_name].append(event)

        themes: list[Theme] = []
        for name, theme_events in grouped.items():
            heat_values = [event.heat_score for event in theme_events]
            current_heat = _bounded(
                max(heat_values) * 0.70
                + sum(heat_values) / len(heat_values) * 0.25
                + min(len(theme_events), 10) * 1.5,
                0,
                100,
            )
            novelty = sum(event.novelty for event in theme_events) / len(theme_events)
            magnitude = (
                sum(event.impact.magnitude for event in theme_events)
                / len(theme_events)
            )
            policy_events = [
                event
                for event in theme_events
                if event.event_type
                in {
                    EventType.POLICY,
                    EventType.MONETARY_POLICY,
                    EventType.REGULATION,
                }
            ]
            policy = _bounded(
                max(
                    [event.importance for event in policy_events],
                    default=0.0,
                ),
                0,
                100,
            )
            capital = _bounded(
                max(
                    (event.impact.capital_score for event in theme_events),
                    default=0.0,
                ),
                0,
                100,
            )
            avg_sentiment = (
                sum(event.sentiment for event in theme_events) / len(theme_events)
            )
            sentiment = _bounded(50 + avg_sentiment * 50, 0, 100)
            previous = existing_by_name.get(name)
            growth = (
                max(0.0, current_heat - previous.current_heat_score)
                if previous is not None
                else 0.0
            )
            forward_heat = _bounded(
                current_heat * 0.42
                + policy * 0.18
                + capital * 0.10
                + magnitude * 0.15
                + novelty * 100 * 0.10
                + growth * 0.05,
                0,
                100,
            )
            priced_in = (
                sum(event.impact.priced_in for event in theme_events)
                / len(theme_events)
            )
            source_coverage = sum(event.source_count for event in theme_events)
            crowding = _bounded(
                current_heat * 0.55
                + min(source_coverage, 20) * 2.0
                + priced_in * 100 * 0.20,
                0,
                100,
            )
            event_type = max(
                theme_events,
                key=lambda event: event.importance,
            ).event_type
            state = _theme_state(
                current_heat=current_heat,
                forward_heat=forward_heat,
                crowding=crowding,
                sentiment=avg_sentiment,
            )
            reasons = [
                f"聚合事件{len(theme_events)}个",
                f"事件最高热度{max(heat_values):.1f}",
                f"政策强度{policy:.1f}",
                f"资金关注{capital:.1f}",
                f"拥挤度{crowding:.1f}",
                "情绪偏多"
                if avg_sentiment > 0.15
                else "情绪偏空"
                if avg_sentiment < -0.15
                else "情绪中性",
            ]
            if growth > 0:
                reasons.append(f"热度较上次提升{growth:.1f}")
            confidence = _bounded(
                sum(event.confidence for event in theme_events) / len(theme_events),
                0,
                1,
            )
            created_at = previous.created_at if previous is not None else current_time
            themes.append(
                Theme(
                    id=previous.id if previous is not None else _theme_id(name),
                    code=previous.code if previous is not None else _theme_code(name),
                    name=name,
                    description="由规则型事件映射聚合得到",
                    state=state,
                    category=_theme_category(event_type),
                    current_heat_score=current_heat,
                    forward_heat_score=forward_heat,
                    crowding_score=crowding,
                    sentiment_score=sentiment,
                    policy_score=policy,
                    capital_score=capital,
                    confidence=confidence,
                    reasons=reasons,
                    event_ids=[event.id for event in theme_events],
                    information_ids=[
                        information_id
                        for event in theme_events
                        for information_id in event.information_ids
                    ],
                    created_at=created_at,
                    updated_at=current_time,
                    model_version=self.model_version,
                )
            )
        return sorted(
            themes,
            key=lambda theme: (
                -theme.current_heat_score,
                -theme.forward_heat_score,
                theme.name,
            ),
        )


class IntelligencePipeline:
    def __init__(
        self,
        *,
        config: PipelineConfig | None = None,
        analyzer: RuleAnalyzer | None = None,
        theme_updater: ThemeHeatUpdater | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.analyzer = analyzer or RuleAnalyzer()
        self.theme_updater = theme_updater or ThemeHeatUpdater()

    def normalize(
        self,
        items: Sequence[RawInformation],
        *,
        now: datetime | None = None,
    ) -> list[RawInformation]:
        return normalize_information_batch(items, now=now)

    def deduplicate(
        self,
        items: Sequence[RawInformation],
    ) -> DeduplicationResult:
        exact = exact_deduplicate(items)
        fuzzy = fuzzy_deduplicate(exact.unique, config=self.config)
        return DeduplicationResult(
            unique=fuzzy.unique,
            duplicates=[*exact.duplicates, *fuzzy.duplicates],
        )

    def cluster_and_analyze(
        self,
        items: Sequence[RawInformation],
    ) -> list[MarketEvent]:
        clusters = cluster_information(items, config=self.config)
        return self.analyzer.analyze_clusters(clusters)

    def update_themes(
        self,
        events: Sequence[MarketEvent],
        *,
        existing: Sequence[Theme] | None = None,
        now: datetime | None = None,
    ) -> list[Theme]:
        return self.theme_updater.update(events, existing=existing, now=now)


def _event_type_priority(event_type: EventType) -> int:
    priorities = {
        EventType.BLACK_SWAN: 18,
        EventType.RISK: 16,
        EventType.MONETARY_POLICY: 15,
        EventType.REGULATION: 14,
        EventType.POLICY: 13,
        EventType.GEOPOLITICAL: 12,
        EventType.MACRO: 11,
        EventType.TECHNOLOGY: 10,
        EventType.EARNINGS: 9,
        EventType.MERGER: 9,
        EventType.ORDER: 8,
        EventType.PRODUCT: 8,
        EventType.PRICE_CHANGE: 8,
        EventType.COMMODITY: 7,
        EventType.COMPANY: 7,
        EventType.INDUSTRY: 6,
        EventType.MARKET: 5,
        EventType.OTHER: 0,
    }
    return priorities.get(event_type, 0)


def _event_type_weight(event_type: EventType) -> float:
    return {
        EventType.BLACK_SWAN: 88.0,
        EventType.RISK: 72.0,
        EventType.MONETARY_POLICY: 78.0,
        EventType.REGULATION: 70.0,
        EventType.POLICY: 74.0,
        EventType.GEOPOLITICAL: 68.0,
        EventType.MACRO: 62.0,
        EventType.TECHNOLOGY: 60.0,
        EventType.EARNINGS: 58.0,
        EventType.MERGER: 56.0,
        EventType.ORDER: 52.0,
        EventType.PRODUCT: 50.0,
        EventType.PRICE_CHANGE: 55.0,
        EventType.COMMODITY: 54.0,
        EventType.COMPANY: 45.0,
        EventType.INDUSTRY: 48.0,
        EventType.MARKET: 42.0,
        EventType.OTHER: 30.0,
    }.get(event_type, 30.0)


def _theme_state(
    *,
    current_heat: float,
    forward_heat: float,
    crowding: float,
    sentiment: float,
) -> ThemeState:
    if current_heat < 8:
        return ThemeState.DEAD
    if crowding >= 80:
        return ThemeState.OVERCROWDED
    if current_heat >= 75 and crowding >= 60:
        return ThemeState.CONSENSUS
    if forward_heat >= 72 and current_heat >= 48:
        return ThemeState.BREAKOUT
    if current_heat >= 55 or forward_heat >= 62:
        return ThemeState.HEATING
    if forward_heat >= 42:
        return ThemeState.INCUBATING
    if sentiment < -0.15 and current_heat < 40:
        return ThemeState.COOLING
    return ThemeState.DISCOVERED


def _theme_category(event_type: EventType) -> ThemeCategory:
    if event_type in {
        EventType.POLICY,
        EventType.MONETARY_POLICY,
        EventType.REGULATION,
    }:
        return ThemeCategory.POLICY
    if event_type in {EventType.MACRO, EventType.GEOPOLITICAL, EventType.COMMODITY}:
        return ThemeCategory.MACRO
    if event_type in {EventType.TECHNOLOGY, EventType.PRODUCT}:
        return ThemeCategory.TECHNOLOGY
    if event_type in {
        EventType.COMPANY,
        EventType.EARNINGS,
        EventType.MERGER,
        EventType.ORDER,
    }:
        return ThemeCategory.COMPANY
    if event_type in {EventType.RISK, EventType.BLACK_SWAN}:
        return ThemeCategory.RISK
    if event_type in {EventType.INDUSTRY, EventType.PRICE_CHANGE}:
        return ThemeCategory.INDUSTRY
    return ThemeCategory.OTHER


def _theme_code(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10].upper()
    return f"TH-{digest}"


def _theme_id(name: str) -> object:
    return uuid5(NAMESPACE_URL, f"https://xquant.local/intelligence/theme/{name}")


def _summary(value: str, limit: int = 360) -> str:
    normalized = normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _bounded(value: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = lower
    if not math.isfinite(parsed):
        parsed = lower
    return max(lower, min(upper, parsed))


__all__ = [
    "DeduplicationResult",
    "IntelligencePipeline",
    "PipelineConfig",
    "RuleAnalyzer",
    "RuleAnalyzerConfig",
    "ThemeHeatUpdater",
    "canonicalize_url",
    "clean_html",
    "cluster_information",
    "exact_deduplicate",
    "fuzzy_deduplicate",
    "information_similarity",
    "jaccard_similarity",
    "normalize_information",
    "normalize_information_batch",
    "normalize_text",
    "tokenize",
]

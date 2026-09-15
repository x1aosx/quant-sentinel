from __future__ import annotations

from datetime import UTC, datetime, timedelta

from xquant.intelligence.domain import (
    EventImpact,
    EventType,
    ImpactDirection,
    MarketEvent,
    RawInformation,
    SourceType,
)
from xquant.intelligence.pipeline import (
    IntelligencePipeline,
    PipelineConfig,
    ThemeHeatUpdater,
    cluster_information,
    exact_deduplicate,
    fuzzy_deduplicate,
    normalize_information,
)


def _information(
    title: str,
    content: str,
    *,
    source: str = "example",
    published_at: datetime | None = None,
    url: str = "https://example.com/news",
) -> RawInformation:
    published = published_at or datetime(2026, 9, 15, 1, 0, tzinfo=UTC)
    return RawInformation(
        source=source,
        source_type=SourceType.NEWS,
        url=url,
        title=title,
        content=content,
        publish_time=published,
        fetch_time=published + timedelta(minutes=1),
    )


def test_normalization_cleans_html_and_enforces_time_order() -> None:
    raw = RawInformation(
        source="publisher",
        source_type=SourceType.NEWS,
        url="https://Example.com/news/?utm_source=x&id=1#section",
        title="<b>政策发布</b>",
        content="<p>支持  人工智能</p><script>ignore()</script>",
        event_time=datetime.fromisoformat("2026-09-15T04:00:00"),
        publish_time=datetime.fromisoformat("2026-09-15T02:00:00"),
        fetch_time=datetime(2026, 9, 15, 1, 0, tzinfo=UTC),
        raw_payload={"publisher": "test"},
    )
    now = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)

    normalized = normalize_information(raw, now=now)

    assert normalized.title == "政策发布"
    assert normalized.content == "支持 人工智能"
    assert normalized.url == "https://example.com/news?id=1"
    assert normalized.process_time == now
    assert normalized.available_time == now
    assert normalized.publish_time <= normalized.process_time
    assert normalized.event_time <= normalized.process_time
    assert normalized.content_hash
    assert normalized.to_dict()["process_time"] == "2026-09-15T03:00:00+00:00"


def test_exact_dedup_uses_content_hash_and_canonical_url() -> None:
    first = _information(
        "同一篇新闻",
        "完全一致的内容",
        url="https://example.com/article?utm_source=a",
    )
    second = _information(
        "同一篇新闻",
        "完全一致的内容",
        url="https://example.com/article?utm_source=b",
    )

    result = exact_deduplicate([first, second])

    assert len(result.unique) == 1
    assert len(result.duplicates) == 1


def test_fuzzy_dedup_is_threshold_configurable() -> None:
    first = _information(
        "央行宣布降准支持市场流动性",
        "央行今日宣布降准，释放长期资金，支持实体经济。",
        url="https://example.com/a",
    )
    second = _information(
        "央行宣布降准，支持市场流动性",
        "央行今日宣布降准，将释放长期资金并支持实体经济。",
        source="mirror",
        url="https://mirror.example.com/b",
    )

    strict = fuzzy_deduplicate(
        [first, second],
        config=PipelineConfig(fuzzy_similarity_threshold=0.98),
    )
    relaxed = fuzzy_deduplicate(
        [first, second],
        config=PipelineConfig(fuzzy_similarity_threshold=0.55),
    )

    assert len(strict.unique) == 2
    assert len(relaxed.unique) == 1
    assert len(relaxed.duplicates) == 1


def test_event_clustering_groups_related_reports() -> None:
    start = datetime(2026, 9, 15, 1, 0, tzinfo=UTC)
    first = _information(
        "多部门发布机器人产业支持政策",
        "政策提出加快人形机器人量产和核心零部件研发。",
        published_at=start,
    )
    second = _information(
        "机器人产业迎来政策支持",
        "政策要求推进人形机器人量产并加强核心零部件研发。",
        source="second-source",
        published_at=start + timedelta(hours=1),
        url="https://second.example.com/robot",
    )
    third = _information(
        "市场指数小幅震荡",
        "主要指数今日窄幅波动，成交额保持平稳。",
        source="market-source",
        published_at=start + timedelta(hours=2),
        url="https://market.example.com/index",
    )

    clusters = cluster_information(
        [first, second, third],
        config=PipelineConfig(cluster_similarity_threshold=0.45),
    )

    assert sorted(len(cluster) for cluster in clusters) == [1, 2]


def test_rule_pipeline_builds_interpretable_theme_heat() -> None:
    now = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)
    items = [
        _information(
            "国务院发布人工智能产业政策",
            "政策支持算力、光模块和数据中心发展，资金关注中际旭创。",
            source="policy",
            published_at=now - timedelta(hours=1),
        ),
        _information(
            "人工智能产业政策落地",
            "政策提出支持算力基础设施和数据中心建设。",
            source="news",
            published_at=now - timedelta(minutes=30),
            url="https://news.example.com/ai",
        ),
    ]
    pipeline = IntelligencePipeline(
        config=PipelineConfig(cluster_similarity_threshold=0.4)
    )

    normalized = pipeline.normalize(items, now=now)
    deduplicated = pipeline.deduplicate(normalized)
    events = pipeline.cluster_and_analyze(deduplicated.unique)
    themes = pipeline.update_themes(events, now=now)

    assert events
    assert events[0].impact.affected_themes == ["AI算力"]
    assert "中际旭创" in events[0].impact.affected_companies
    assert "300308" in events[0].impact.affected_stocks
    assert themes
    theme = themes[0]
    assert theme.name == "AI算力"
    assert 0 <= theme.current_heat_score <= 100
    assert 0 <= theme.forward_heat_score <= 100
    assert 0 <= theme.crowding_score <= 100
    assert theme.reasons
    assert theme.to_dict()["current_heat"] == theme.current_heat_score


def test_theme_heat_updater_handles_negative_crowding_signal() -> None:
    now = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)
    event = MarketEvent(
        event_type=EventType.RISK,
        title="主题过热并出现风险信号",
        summary="资金拥挤，监管提示风险",
        importance=90,
        sentiment=-0.8,
        confidence=0.9,
        novelty=0.7,
        impact_direction=ImpactDirection.NEGATIVE,
        heat_score=95,
        source_count=10,
        impact=EventImpact(
            direction=ImpactDirection.NEGATIVE,
            magnitude=85,
            confidence=0.9,
            novelty=0.7,
            priced_in=0.8,
            affected_themes=["测试主题"],
            capital_score=90,
        ),
    )

    themes = ThemeHeatUpdater().update([event], now=now)

    assert themes[0].state.value == "OVERCROWDED"
    assert themes[0].crowding_score >= 80
    assert "拥挤度" in " ".join(themes[0].reasons)

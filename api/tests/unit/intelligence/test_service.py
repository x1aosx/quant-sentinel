from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from xquant.intelligence.domain import RawInformation, SourceType
from xquant.intelligence.pipeline import IntelligencePipeline, PipelineConfig
from xquant.intelligence.providers import InMemoryInformationSource
from xquant.intelligence.repository import SqliteIntelligenceRepository
from xquant.intelligence.service import IntelligenceService
from xquant.intelligence.tasks import register_intelligence_tasks
from xquant.registry.sqlite import Database
from xquant.scheduler.application import TaskRegistry


def test_service_runs_collect_process_and_morning_brief_end_to_end(tmp_path) -> None:
    now = datetime(2026, 9, 15, 3, 0, tzinfo=UTC)
    source = InMemoryInformationSource(
        [
            RawInformation(
                source="policy",
                source_type=SourceType.POLICY,
                url="https://policy.example.com/ai",
                title="国务院发布人工智能产业政策",
                content="政策支持算力和数据中心建设，资金关注中际旭创。",
                publish_time=now - timedelta(hours=1),
                fetch_time=now - timedelta(minutes=59),
            ),
            RawInformation(
                source="mirror",
                source_type=SourceType.NEWS,
                url="https://mirror.example.com/ai",
                title="国务院发布人工智能产业政策",
                content="政策支持算力和数据中心建设，资金关注中际旭创。",
                publish_time=now - timedelta(minutes=50),
                fetch_time=now - timedelta(minutes=49),
            ),
            RawInformation(
                source="risk",
                source_type=SourceType.NEWS,
                url="https://risk.example.com/event",
                title="海外冲突升级引发原油风险",
                content="地缘冲突升级，市场担忧原油供应和航运风险。",
                publish_time=now - timedelta(minutes=20),
                fetch_time=now - timedelta(minutes=19),
            ),
        ]
    )
    repository = SqliteIntelligenceRepository(Database(tmp_path / "legacy.db"))
    service = IntelligenceService(
        repository,
        [source],
        pipeline=IntelligencePipeline(
            config=PipelineConfig(fuzzy_similarity_threshold=0.9)
        ),
    )

    async def run() -> dict:
        return await service.run(
            now - timedelta(hours=2),
            now,
            now=now,
        )

    result = asyncio.run(run())

    assert result["collected"] == 3
    assert result["unique"] == 2
    assert result["duplicates"] == 1
    assert result["events"] >= 2
    assert result["themes"] >= 2
    brief = service.get_morning_brief()
    assert brief is not None
    assert brief.brief_type.value == "MORNING"
    assert brief.available_time >= brief.generated_at
    assert service.overview()["counts"]["information"] == 3


def test_register_intelligence_tasks_uses_required_names(tmp_path) -> None:
    repository = SqliteIntelligenceRepository(Database(tmp_path / "legacy.db"))
    service = IntelligenceService(repository, [])
    registry = TaskRegistry()

    register_intelligence_tasks(registry, service)

    names = {definition.name for definition in registry.list()}
    assert names == {
        "intelligence.collect",
        "intelligence.process",
        "intelligence.brief.morning",
    }

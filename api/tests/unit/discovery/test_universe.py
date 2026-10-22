from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from xquant.discovery import (
    ORIGIN_INTELLIGENCE,
    ORIGIN_MARKET_RANKING,
    STATUS_EXISTING,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SYNCED,
    EastmoneyRankingSource,
    EventSnapshot,
    RankingBoard,
    SinaRankingSource,
    StockSnapshot,
    ThemeSnapshot,
    UniverseLead,
    UniverseScanner,
    intelligence_leads,
    normalize_a_share_symbol,
)


class FakeIntelSource:
    def __init__(
        self,
        events: Sequence[EventSnapshot] = (),
        themes: Sequence[ThemeSnapshot] = (),
    ) -> None:
        self.events = list(events)
        self.themes = list(themes)

    def list_event_snapshots(self) -> Sequence[EventSnapshot]:
        return self.events

    def list_theme_snapshots(self) -> Sequence[ThemeSnapshot]:
        return self.themes


class FakeDatabase:
    def __init__(self, symbols: Sequence[str] = ()) -> None:
        self.symbols = list(symbols)
        self.synced: list[str] = []

    def list_datasets(self) -> list[dict[str, Any]]:
        return [{"id": f"dataset-{symbol}", "symbol": symbol} for symbol in self.symbols]

    def sync_dataset(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        symbol = str(payload["symbol"])
        self.synced.append(symbol)
        self.symbols.append(symbol)
        return {"id": f"dataset-{symbol}", "symbol": symbol, "title": f"{symbol} 公司"}


class FakeRankingSource:
    name = "fake_ranking"

    def __init__(self, leads: Sequence[UniverseLead] = (), error: Exception | None = None) -> None:
        self.leads = list(leads)
        self.error = error

    def discover(self) -> Sequence[UniverseLead]:
        if self.error is not None:
            raise self.error
        return self.leads


def _event(
    event_id: str,
    direction: str,
    symbols: Sequence[str],
    *,
    importance: float = 90.0,
    magnitude: float = 80.0,
    themes: Sequence[str] = (),
) -> EventSnapshot:
    return EventSnapshot(
        event_id=event_id,
        direction=direction,
        affected_stock_ids=list(symbols),
        affected_theme_ids=list(themes),
        importance=importance,
        magnitude=magnitude,
    )


def test_normalize_a_share_symbol_accepts_common_spellings() -> None:
    assert normalize_a_share_symbol("600519") == "600519"
    assert normalize_a_share_symbol("SH600519") == "600519"
    assert normalize_a_share_symbol("600519.SS") == "600519"
    assert normalize_a_share_symbol("sz000001") == "000001"
    assert normalize_a_share_symbol("300750.SZ") == "300750"


def test_normalize_a_share_symbol_rejects_non_tradable_codes() -> None:
    assert normalize_a_share_symbol("830799") is None
    assert normalize_a_share_symbol("430047") is None
    assert normalize_a_share_symbol("AAPL") is None
    assert normalize_a_share_symbol("") is None


def test_intelligence_leads_keeps_positive_events_and_drops_negative_only() -> None:
    events = [
        _event("e1", "POSITIVE", ["600519", "SH600519"], themes=["t1"]),
        _event("e2", "NEGATIVE", ["000001"], importance=90.0, magnitude=90.0),
    ]
    themes = [ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=80.0)]

    leads = intelligence_leads(events, themes)

    symbols = {lead.symbol for lead in leads}
    assert symbols == {"600519"}
    lead = leads[0]
    assert lead.origin == ORIGIN_INTELLIGENCE
    assert lead.signals["intelligence_score"] > 50
    assert any("白酒" in reason for reason in lead.reasons)


def test_eastmoney_ranking_source_filters_excluded_names_and_low_amount() -> None:
    calls: list[Mapping[str, Any]] = []

    def fake_http(
        url: str, params: Mapping[str, Any], headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        calls.append(params)
        return {
            "data": {
                "diff": [
                    {"f12": "600519", "f14": "贵州茅台", "f6": 2e9, "f3": 3.2, "f62": 1e8},
                    {"f12": "000001", "f14": "ST平安", "f6": 2e9, "f3": 4.0},
                    {"f12": "830799", "f14": "北交所股", "f6": 2e9, "f3": 5.0},
                    {"f12": "300750", "f14": "宁德时代", "f6": 1e6, "f3": 6.0},
                ]
            }
        }

    source = EastmoneyRankingSource(
        boards=[RankingBoard("change", "涨幅榜", "f3")],
        size=10,
        min_amount=5e7,
        http_json=fake_http,
    )

    leads = source.discover()

    assert [lead.symbol for lead in leads] == ["600519"]
    assert leads[0].origin == ORIGIN_MARKET_RANKING
    assert leads[0].signals["rank_score"] == 100.0
    assert calls and calls[0]["fid"] == "f3"


def test_scanner_syncs_new_symbols_and_marks_existing() -> None:
    database = FakeDatabase(["000001"])
    intel = FakeIntelSource(
        events=[
            _event("e1", "POSITIVE", ["600519"], themes=["t1"]),
            _event("e2", "POSITIVE", ["000001"], themes=["t1"]),
        ],
        themes=[ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=85.0)],
    )
    scanner = UniverseScanner(intel, database, min_score=55.0)

    report = scanner.scan(sync=True)

    by_symbol = {candidate.symbol: candidate for candidate in report.candidates}
    assert by_symbol["600519"].status == STATUS_SYNCED
    assert by_symbol["600519"].dataset_id == "dataset-600519"
    assert by_symbol["000001"].status == STATUS_EXISTING
    assert database.synced == ["600519"]
    assert report.synced_count == 1


def test_scanner_respects_sync_limit_and_leaves_rest_pending() -> None:
    database = FakeDatabase()
    events = [
        _event(f"e{index}", "POSITIVE", [symbol], themes=["t1"])
        for index, symbol in enumerate(["600519", "000001", "300750"], start=1)
    ]
    intel = FakeIntelSource(
        events=events,
        themes=[ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=90.0)],
    )
    scanner = UniverseScanner(intel, database, max_sync=1)

    report = scanner.scan(sync=True)

    statuses = [candidate.status for candidate in report.candidates]
    assert statuses.count(STATUS_SYNCED) == 1
    assert statuses.count(STATUS_PENDING) == 2
    assert len(database.synced) == 1


def test_scanner_degrades_when_external_source_fails() -> None:
    database = FakeDatabase()
    intel = FakeIntelSource(
        events=[_event("e1", "POSITIVE", ["600519"], themes=["t1"])],
        themes=[ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=80.0)],
    )
    scanner = UniverseScanner(
        intel,
        database,
        sources=[FakeRankingSource(error=RuntimeError("榜单接口不可用"))],
    )

    report = scanner.scan(sync=True)

    assert any("榜单接口不可用" in warning for warning in report.warnings)
    assert [candidate.symbol for candidate in report.candidates] == ["600519"]
    assert database.synced == ["600519"]


def test_scanner_rejects_candidate_when_sync_fails() -> None:
    database = FakeDatabase()

    def failing_sync(symbol: str) -> Mapping[str, Any]:
        raise ValueError(f"{symbol} 无行情")

    intel = FakeIntelSource(
        events=[_event("e1", "POSITIVE", ["600519"], themes=["t1"])],
        themes=[ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=80.0)],
    )
    scanner = UniverseScanner(intel, database, sync=failing_sync)

    report = scanner.scan(sync=True)

    assert report.candidates[0].status == STATUS_REJECTED
    assert report.synced_count == 0
    assert any("无行情" in warning for warning in report.warnings)


def test_scanner_skips_candidates_below_min_score() -> None:
    database = FakeDatabase()
    intel = FakeIntelSource(
        events=[_event("e1", "NEUTRAL", ["600519"], importance=10.0, magnitude=10.0)],
        themes=[],
    )
    scanner = UniverseScanner(intel, database, min_score=55.0)

    report = scanner.scan()

    assert report.candidates == []
    assert database.synced == []


def test_scanner_never_syncs_excluded_names_from_ranking() -> None:
    database = FakeDatabase()
    ranking = FakeRankingSource(
        leads=[
            UniverseLead(
                symbol="600519",
                name="*ST茅台",
                origin=ORIGIN_MARKET_RANKING,
                signals={"rank_score": 95.0},
            )
        ]
    )
    scanner = UniverseScanner(FakeIntelSource(), database, sources=[ranking])

    report = scanner.scan()

    assert report.candidates == []
    assert database.synced == []


def test_sina_ranking_source_maps_fields_and_filters() -> None:
    def fake_http(url: str, params: Mapping[str, Any], headers: Mapping[str, str]) -> Any:
        return [
            {
                "code": "300750",
                "name": "宁德时代",
                "changepercent": 2.5,
                "amount": 3e9,
                "turnoverratio": 1.2,
                "per": 25.4,
                "mktcap": 1.2e7,
            },
            {"code": "301718", "name": "N通则", "amount": 3e9},
            {"code": "600519", "name": "贵州茅台", "amount": 1e6},
        ]

    source = SinaRankingSource(
        boards=[RankingBoard("change", "涨幅榜", "changepercent")],
        size=10,
        min_amount=5e7,
        http_json=fake_http,
    )

    leads = source.discover()

    assert [lead.symbol for lead in leads] == ["300750"]
    assert leads[0].name == "宁德时代"
    assert leads[0].origin == ORIGIN_MARKET_RANKING
    assert leads[0].signals["amount"] == 3e9
    assert leads[0].signals["market_cap"] == 1.2e11


def test_sina_ranking_source_rejects_non_array_payload() -> None:
    def fake_http(url: str, params: Mapping[str, Any], headers: Mapping[str, str]) -> Any:
        return {"error": "blocked"}

    source = SinaRankingSource(http_json=fake_http)

    try:
        source.discover()
    except TypeError as exc:
        assert "数组" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("应当拒绝非数组响应")


def test_scanner_merges_corroborating_sources_with_bonus() -> None:
    database = FakeDatabase()
    intel = FakeIntelSource(
        events=[_event("e1", "POSITIVE", ["600519"], themes=["t1"])],
        themes=[ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=80.0)],
    )
    ranking = FakeRankingSource(
        leads=[
            UniverseLead(
                symbol="600519",
                name="贵州茅台",
                origin=ORIGIN_MARKET_RANKING,
                signals={"rank_score": 90.0},
            )
        ]
    )
    scanner = UniverseScanner(intel, database, sources=[ranking])

    report = scanner.scan(sync=False)

    candidate = report.candidates[0]
    assert candidate.origin == f"{ORIGIN_INTELLIGENCE}+{ORIGIN_MARKET_RANKING}"
    assert candidate.status == STATUS_PENDING
    assert candidate.score > 90.0
    assert candidate.name == "贵州茅台"
    assert database.synced == []


def test_report_round_trips_through_dict() -> None:
    database = FakeDatabase()
    intel = FakeIntelSource(
        events=[_event("e1", "POSITIVE", ["600519"], themes=["t1"])],
        themes=[ThemeSnapshot(theme_id="t1", name="白酒", current_heat_score=80.0)],
    )
    scanner = UniverseScanner(intel, database)
    report = scanner.scan()

    payload = report.to_dict()

    assert payload["scanned_at"] == report.scanned_at.isoformat()
    assert payload["candidates"][0]["symbol"] == "600519"
    assert isinstance(payload["candidates"][0]["discovered_at"], str)
    assert payload["model_version"] == "discovery-universe-v1"
    assert datetime.fromisoformat(payload["scanned_at"]).tzinfo is not None
    assert datetime.fromisoformat(payload["scanned_at"]).date() == datetime.now(UTC).date()


def test_scanner_ignores_non_a_share_snapshots() -> None:
    database = FakeDatabase(["AAPL", "BTC-USD"])
    scanner = UniverseScanner(FakeIntelSource(), database)

    report = scanner.scan()

    assert report.dataset_count == 0
    assert report.candidates == []


def test_stock_snapshot_fixture_is_irrelevant_to_universe() -> None:
    """Guards the fake input types used above against silent domain drift."""

    snapshot = StockSnapshot(stock_id="600519", name="贵州茅台")
    assert snapshot.stock_id == "600519"

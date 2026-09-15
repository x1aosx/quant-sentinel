from __future__ import annotations

from collections.abc import Sequence

from xquant.discovery import (
    CandidateState,
    DiscoveryCandidate,
    DiscoveryService,
    MarketRegime,
    StockSnapshot,
)


class FakeSource:
    def __init__(self, stocks: Sequence[StockSnapshot]) -> None:
        self.stocks = list(stocks)

    def list_stock_snapshots(self) -> Sequence[StockSnapshot]:
        return self.stocks

    def list_theme_snapshots(self) -> Sequence:
        return []

    def list_event_snapshots(self) -> Sequence:
        return []


class FakeRepository:
    def __init__(self) -> None:
        self.candidates: dict[str, DiscoveryCandidate] = {}
        self.save_batches = 0

    def list_candidates(self) -> Sequence[DiscoveryCandidate]:
        return list(self.candidates.values())

    def save_candidates(self, candidates: Sequence[DiscoveryCandidate]) -> int:
        self.save_batches += 1
        for candidate in candidates:
            self.candidates[candidate.stock_id] = candidate
        return len(candidates)


def _stock(stock_id: str, score: float) -> StockSnapshot:
    names = (
        "market_fit",
        "theme",
        "forward_theme",
        "policy",
        "global_event",
        "sentiment",
        "attention_momentum",
        "capital",
        "price_action",
        "alpha",
        "fundamental",
        "liquidity",
        "crowding",
        "risk",
    )
    return StockSnapshot(
        stock_id=stock_id,
        scores={name: score for name in names},
        confidence=0.8,
    )


def test_service_runs_with_protocols_and_persists_state() -> None:
    source = FakeSource([_stock("A", 75)])
    repository = FakeRepository()
    service = DiscoveryService(
        source,
        repository,
        top_percentile=1.0,
        focus_percentile=1.0,
        watch_percentile=1.0,
    )

    first = service.run(MarketRegime.BULL)
    assert first.model_version == "discovery-mvp-v1"
    assert first.selected_count == 1
    assert repository.candidates["A"].state == CandidateState.WATCH
    created_at = repository.candidates["A"].created_at

    second = service.run(MarketRegime.BULL)
    assert second.candidates[0].state == CandidateState.FOCUS
    assert repository.candidates["A"].created_at == created_at
    assert repository.save_batches == 2

    source.stocks = []
    third = service.run(MarketRegime.BULL)
    assert third.selected_count == 0
    assert repository.candidates["A"].state == CandidateState.COOLDOWN
    assert third.stale_candidates[0].stock_id == "A"

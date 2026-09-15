from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .domain import (
    CandidateState,
    DiscoveryCandidate,
    DiscoveryResult,
    DiscoveryWeights,
    EventSnapshot,
    MarketRegime,
    StockSnapshot,
    ThemeSnapshot,
    utc_now,
)
from .engine import DiscoveryEngine, MODEL_VERSION
from .state_machine import CandidateStateMachine
from .weights import dynamic_weights


@runtime_checkable
class DiscoveryDataSource(Protocol):
    def list_stock_snapshots(self) -> Sequence[StockSnapshot]:
        ...

    def list_theme_snapshots(self) -> Sequence[ThemeSnapshot]:
        ...

    def list_event_snapshots(self) -> Sequence[EventSnapshot]:
        ...


@runtime_checkable
class DiscoveryCandidateRepository(Protocol):
    def list_candidates(self) -> Sequence[DiscoveryCandidate]:
        ...

    def save_candidates(self, candidates: Sequence[DiscoveryCandidate]) -> Any:
        ...


CandidatePersistence = DiscoveryCandidateRepository


class DiscoveryService:
    """Application service that connects snapshots and candidate persistence."""

    def __init__(
        self,
        data_source: DiscoveryDataSource,
        repository: DiscoveryCandidateRepository | None = None,
        *,
        weights: DiscoveryWeights | None = None,
        weight_overrides: Mapping[str, float] | None = None,
        model_version: str = MODEL_VERSION,
        top_n: int | None = None,
        top_percentile: float | None = 0.20,
        focus_percentile: float = 0.10,
        watch_percentile: float = 0.30,
        state_machine: CandidateStateMachine | None = None,
    ) -> None:
        if top_n is not None and top_percentile is not None:
            raise ValueError("specify either top_n or top_percentile, not both")
        self.data_source = data_source
        self.repository = repository
        self.weights = weights
        self.weight_overrides = dict(weight_overrides or {})
        self.model_version = model_version
        self.top_n = top_n
        self.top_percentile = top_percentile
        self.focus_percentile = focus_percentile
        self.watch_percentile = watch_percentile
        self.state_machine = state_machine or CandidateStateMachine()

    def discover(
        self,
        regime: MarketRegime | str = MarketRegime.SIDEWAYS,
        *,
        generated_at: datetime | None = None,
    ) -> DiscoveryResult:
        market_regime = MarketRegime(regime)
        stocks = list(self.data_source.list_stock_snapshots())
        themes = list(self.data_source.list_theme_snapshots())
        events = list(self.data_source.list_event_snapshots())
        current = generated_at or utc_now()

        weights = self.weights or dynamic_weights(
            market_regime,
            self.weight_overrides,
            model_version=self.model_version,
        )
        engine = DiscoveryEngine(weights=weights, model_version=self.model_version)
        result = engine.discover(
            stocks,
            themes,
            events,
            regime=market_regime,
            generated_at=current,
            top_n=self.top_n,
            top_percentile=self.top_percentile,
        )

        existing = self._existing_candidates()
        result.rankings = [
            self._apply_ranked_state(candidate, existing.get(candidate.stock_id), current)
            for candidate in result.rankings
        ]
        active_ids = {candidate.stock_id for candidate in result.rankings}
        result.candidates = [
            candidate for candidate in result.rankings if candidate.selected
        ]
        result.selected_count = len(result.candidates)
        result.stale_candidates = [
            self._apply_stale_state(candidate, current)
            for stock_id, candidate in existing.items()
            if stock_id not in active_ids
        ]

        if self.repository is not None:
            selected_ids = {candidate.stock_id for candidate in result.candidates}
            cooled = [
                candidate
                for candidate in result.rankings
                if candidate.stock_id not in selected_ids
                and candidate.stock_id in existing
            ]
            tracked = _deduplicate_candidates(
                [*result.candidates, *cooled, *result.stale_candidates]
            )
            self.repository.save_candidates(tracked)
        return result

    def run(
        self,
        regime: MarketRegime | str = MarketRegime.SIDEWAYS,
        *,
        generated_at: datetime | None = None,
    ) -> DiscoveryResult:
        return self.discover(regime, generated_at=generated_at)

    def _existing_candidates(self) -> dict[str, DiscoveryCandidate]:
        if self.repository is None:
            return {}
        return {
            candidate.stock_id: candidate
            for candidate in self.repository.list_candidates()
        }

    def _apply_ranked_state(
        self,
        candidate: DiscoveryCandidate,
        previous: DiscoveryCandidate | None,
        current: datetime,
    ) -> DiscoveryCandidate:
        source_state = (
            CandidateState(previous.state)
            if previous is not None
            else CandidateState.DISCOVERED
        )
        target = self.state_machine.next_state(
            source_state,
            selected=candidate.selected,
            rank_percentile=candidate.rank_percentile,
            focus_percentile=self.focus_percentile,
            watch_percentile=self.watch_percentile,
        )
        updated = replace(
            candidate,
            state=source_state,
            created_at=previous.created_at if previous is not None else current,
        )
        return self.state_machine.apply(
            updated,
            target,
            reason=(
                f"横截面排名前 {candidate.rank_percentile:.2%}"
                if candidate.selected
                else "未进入当前候选池"
            ),
            updated_at=current,
        )

    def _apply_stale_state(
        self,
        candidate: DiscoveryCandidate,
        current: datetime,
    ) -> DiscoveryCandidate:
        target = self.state_machine.next_state(
            candidate.state,
            selected=False,
            rank_percentile=1.0,
            focus_percentile=self.focus_percentile,
            watch_percentile=self.watch_percentile,
        )
        if target == CandidateState(candidate.state):
            return candidate
        return self.state_machine.apply(
            candidate,
            target,
            reason="当前市场快照不再包含该股票",
            updated_at=current,
        )


def _deduplicate_candidates(
    candidates: Sequence[DiscoveryCandidate],
) -> list[DiscoveryCandidate]:
    result: list[DiscoveryCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.stock_id in seen:
            continue
        seen.add(candidate.stock_id)
        result.append(candidate)
    return result

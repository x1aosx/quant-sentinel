from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime

from .domain import CandidateState, DiscoveryCandidate, ensure_utc, utc_now


class InvalidStateTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: Mapping[CandidateState, frozenset[CandidateState]] = {
    CandidateState.DISCOVERED: frozenset(
        {
            CandidateState.WATCH,
            CandidateState.COOLDOWN,
            CandidateState.REMOVE,
        }
    ),
    CandidateState.WATCH: frozenset(
        {
            CandidateState.FOCUS,
            CandidateState.DISCOVERED,
            CandidateState.COOLDOWN,
            CandidateState.REMOVE,
        }
    ),
    CandidateState.FOCUS: frozenset(
        {
            CandidateState.DEEP_ANALYSIS,
            CandidateState.WATCH,
            CandidateState.COOLDOWN,
            CandidateState.REMOVE,
        }
    ),
    CandidateState.DEEP_ANALYSIS: frozenset(
        {
            CandidateState.SIGNAL_READY,
            CandidateState.FOCUS,
            CandidateState.COOLDOWN,
            CandidateState.REMOVE,
        }
    ),
    CandidateState.SIGNAL_READY: frozenset(
        {
            CandidateState.COOLDOWN,
            CandidateState.FOCUS,
            CandidateState.REMOVE,
        }
    ),
    CandidateState.COOLDOWN: frozenset(
        {
            CandidateState.WATCH,
            CandidateState.DISCOVERED,
            CandidateState.REMOVE,
        }
    ),
    CandidateState.REMOVE: frozenset({CandidateState.DISCOVERED}),
}

PROMOTIONS: Mapping[CandidateState, CandidateState] = {
    CandidateState.DISCOVERED: CandidateState.WATCH,
    CandidateState.WATCH: CandidateState.FOCUS,
    CandidateState.FOCUS: CandidateState.DEEP_ANALYSIS,
    CandidateState.DEEP_ANALYSIS: CandidateState.SIGNAL_READY,
    CandidateState.COOLDOWN: CandidateState.WATCH,
}

DEMOTIONS: Mapping[CandidateState, CandidateState] = {
    CandidateState.WATCH: CandidateState.DISCOVERED,
    CandidateState.FOCUS: CandidateState.WATCH,
    CandidateState.DEEP_ANALYSIS: CandidateState.FOCUS,
    CandidateState.SIGNAL_READY: CandidateState.FOCUS,
    CandidateState.COOLDOWN: CandidateState.DISCOVERED,
}


class CandidateStateMachine:
    """Validated candidate lifecycle transitions.

    Ranking decides whether a candidate advances. Scores are intentionally not
    used as a fixed cutoff in this class or in the discovery engine.
    """

    def can_transition(
        self,
        current: CandidateState | str,
        target: CandidateState | str,
    ) -> bool:
        source = CandidateState(current)
        destination = CandidateState(target)
        return source == destination or destination in ALLOWED_TRANSITIONS[source]

    def transition(
        self,
        current: CandidateState | str,
        target: CandidateState | str,
    ) -> CandidateState:
        source = CandidateState(current)
        destination = CandidateState(target)
        if not self.can_transition(source, destination):
            raise InvalidStateTransition(
                f"cannot transition candidate from {source.value} to {destination.value}"
            )
        return destination

    def apply(
        self,
        candidate: DiscoveryCandidate,
        target: CandidateState | str,
        *,
        reason: str = "",
        updated_at: datetime | None = None,
    ) -> DiscoveryCandidate:
        destination = self.transition(candidate.state, target)
        current_time = ensure_utc(updated_at, field_name="updated_at")
        if current_time > candidate.updated_at:
            candidate_time = current_time
        else:
            candidate_time = candidate.updated_at
        history = list(candidate.metadata.get("state_history", []))
        history.append(
            {
                "from": candidate.state.value,
                "to": destination.value,
                "reason": reason,
                "at": candidate_time.isoformat(),
            }
        )
        metadata = {**candidate.metadata, "state_history": history}
        return replace(
            candidate,
            state=destination,
            updated_at=candidate_time,
            metadata=metadata,
        )

    def promote(
        self,
        candidate: DiscoveryCandidate,
        *,
        reason: str = "横截面排名晋级",
        updated_at: datetime | None = None,
    ) -> DiscoveryCandidate:
        target = PROMOTIONS.get(CandidateState(candidate.state))
        if target is None:
            return candidate
        return self.apply(candidate, target, reason=reason, updated_at=updated_at)

    def demote(
        self,
        candidate: DiscoveryCandidate,
        *,
        reason: str = "横截面排名降级",
        updated_at: datetime | None = None,
    ) -> DiscoveryCandidate:
        target = DEMOTIONS.get(CandidateState(candidate.state))
        if target is None:
            return candidate
        return self.apply(candidate, target, reason=reason, updated_at=updated_at)

    def reenter(
        self,
        candidate: DiscoveryCandidate,
        *,
        reason: str = "候选重新进入发现池",
        updated_at: datetime | None = None,
    ) -> DiscoveryCandidate:
        if CandidateState(candidate.state) != CandidateState.REMOVE:
            return candidate
        return self.apply(
            candidate,
            CandidateState.DISCOVERED,
            reason=reason,
            updated_at=updated_at,
        )

    def remove(
        self,
        candidate: DiscoveryCandidate,
        *,
        reason: str = "候选失效",
        updated_at: datetime | None = None,
    ) -> DiscoveryCandidate:
        if CandidateState(candidate.state) == CandidateState.REMOVE:
            return candidate
        return self.apply(
            candidate,
            CandidateState.REMOVE,
            reason=reason,
            updated_at=updated_at,
        )

    def next_state(
        self,
        current: CandidateState | str,
        *,
        selected: bool,
        rank_percentile: float,
        focus_percentile: float = 0.10,
        watch_percentile: float = 0.30,
    ) -> CandidateState:
        source = CandidateState(current)
        percentile = float(rank_percentile)
        if not 0 <= percentile <= 1:
            raise ValueError("rank_percentile must be between 0 and 1")
        if not 0 < focus_percentile <= watch_percentile <= 1:
            raise ValueError(
                "percentile thresholds must satisfy "
                "0 < focus_percentile <= watch_percentile <= 1"
            )

        if not selected:
            if source == CandidateState.REMOVE:
                return CandidateState.REMOVE
            if source == CandidateState.COOLDOWN:
                return CandidateState.REMOVE
            return CandidateState.COOLDOWN

        if source == CandidateState.REMOVE:
            return CandidateState.DISCOVERED
        if source in {CandidateState.DEEP_ANALYSIS, CandidateState.SIGNAL_READY}:
            return source
        if source in {CandidateState.DISCOVERED, CandidateState.COOLDOWN}:
            return CandidateState.WATCH
        if source == CandidateState.WATCH and percentile <= focus_percentile:
            return CandidateState.FOCUS
        if source == CandidateState.FOCUS:
            if percentile <= focus_percentile:
                return CandidateState.DEEP_ANALYSIS
            if percentile > watch_percentile:
                return CandidateState.WATCH
        return source


DEFAULT_STATE_MACHINE = CandidateStateMachine()


def transition_candidate(
    candidate: DiscoveryCandidate,
    target: CandidateState | str,
    *,
    reason: str = "",
    updated_at: datetime | None = None,
) -> DiscoveryCandidate:
    return DEFAULT_STATE_MACHINE.apply(
        candidate,
        target,
        reason=reason,
        updated_at=updated_at or utc_now(),
    )

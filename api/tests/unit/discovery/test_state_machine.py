from __future__ import annotations

from pytest import raises

from xquant.discovery import (
    CandidateState,
    CandidateStateMachine,
    DiscoveryCandidate,
    InvalidStateTransition,
)


def test_state_machine_supports_promotion_demotion_and_reentry() -> None:
    machine = CandidateStateMachine()
    candidate = DiscoveryCandidate(stock_id="A", confidence=0.8)

    watch = machine.promote(candidate)
    assert watch.state == CandidateState.WATCH
    focus = machine.promote(watch)
    assert focus.state == CandidateState.FOCUS
    demoted = machine.demote(focus)
    assert demoted.state == CandidateState.WATCH
    removed = machine.apply(demoted, CandidateState.REMOVE, reason="失效")
    assert removed.state == CandidateState.REMOVE
    reentered = machine.reenter(removed)
    assert reentered.state == CandidateState.DISCOVERED


def test_state_machine_rejects_skipping_required_states() -> None:
    machine = CandidateStateMachine()
    with raises(InvalidStateTransition):
        machine.transition(CandidateState.DISCOVERED, CandidateState.SIGNAL_READY)


def test_rank_based_next_state_does_not_use_score_thresholds() -> None:
    machine = CandidateStateMachine()
    assert (
        machine.next_state(
            CandidateState.WATCH,
            selected=True,
            rank_percentile=0.05,
            focus_percentile=0.10,
        )
        == CandidateState.FOCUS
    )
    assert (
        machine.next_state(
            CandidateState.FOCUS,
            selected=True,
            rank_percentile=0.50,
            focus_percentile=0.10,
            watch_percentile=0.30,
        )
        == CandidateState.WATCH
    )
    assert (
        machine.next_state(
            CandidateState.WATCH,
            selected=False,
            rank_percentile=1.0,
        )
        == CandidateState.COOLDOWN
    )

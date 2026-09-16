from __future__ import annotations

from pytest import approx

from xquant.discovery import (
    CandidateState,
    DiscoveryEngine,
    EventSnapshot,
    MarketRegime,
    StockSnapshot,
    ThemeSnapshot,
)


def _stock(stock_id: str, score: float = 50.0) -> StockSnapshot:
    return StockSnapshot(
        stock_id=stock_id,
        name=stock_id,
        theme_ids=["semiconductors"],
        scores={
            "market_fit": score,
            "theme": score,
            "forward_theme": score,
            "policy": score,
            "global_event": score,
            "sentiment": score,
            "attention_momentum": score,
            "capital": score,
            "price_action": score,
            "alpha": score,
            "fundamental": score,
            "liquidity": score,
            "crowding": 20.0,
            "risk": 20.0,
        },
        confidence=0.8,
    )


def _theme() -> ThemeSnapshot:
    return ThemeSnapshot(
        theme_id="semiconductors",
        name="半导体",
        state="HEATING",
        stock_ids=["A", "B"],
        current_heat_score=82,
        forward_heat_score=88,
        crowding_score=35,
        sentiment_score=78,
        policy_score=84,
        capital_score=80,
        attention_momentum_score=91,
        confidence=0.9,
        reasons=["政策与订单同时加速"],
    )


def test_cross_sectional_ranking_uses_deterministic_tie_breaks() -> None:
    engine = DiscoveryEngine()
    result = engine.discover(
        [_stock("B"), _stock("A")],
        [_theme()],
        regime=MarketRegime.BULL,
        top_n=1,
    )

    assert [candidate.stock_id for candidate in result.rankings] == ["A", "B"]
    assert result.candidates[0].stock_id == "A"
    assert result.rankings[0].rank == 1
    assert result.rankings[1].rank == 2
    assert result.rankings[0].rank_percentile == approx(0.5)
    assert result.candidates[0].state == CandidateState.DISCOVERED


def test_top_percentile_is_cross_sectional_not_a_fixed_score_threshold() -> None:
    engine = DiscoveryEngine()
    result = engine.discover(
        [_stock("A", 20), _stock("B", 10), _stock("C", 5)],
        [_theme()],
        top_percentile=0.33,
    )

    assert result.selected_count == 1
    assert result.rankings[0].discovery_score < 80


def test_candidates_keep_complete_score_and_explanation_fields() -> None:
    event = EventSnapshot(
        event_id="event-1",
        title="海外资本开支上调",
        event_type="INDUSTRY",
        direction="POSITIVE",
        affected_theme_ids=["semiconductors"],
        importance=90,
        magnitude=80,
        novelty=70,
        priced_in=20,
        confidence=0.85,
        reasons=["订单指引上调"],
        risks=["兑现节奏可能延后"],
    )
    candidate = DiscoveryEngine().score_stock(_stock("A"), [_theme()], [event])

    assert candidate.reasons
    assert candidate.risks
    assert 0 <= candidate.discovery_score <= 100
    assert 0 <= candidate.confidence <= 1
    assert candidate.model_version == "discovery-mvp-v1"
    assert len(candidate.score_components) == 14
    for component in candidate.score_components:
        assert 0 <= component.score <= 100
        assert component.weight >= 0
        assert component.reasons
        assert component.risks
        sign = -1 if component.penalty else 1
        assert component.weighted_value == approx(
            sign * component.score * component.weight
        )

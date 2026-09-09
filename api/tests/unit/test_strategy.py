from __future__ import annotations

from datetime import datetime

from xquant.domain.models import EvaluationContext, IntentAction
from xquant.strategies.srpa_breakout_retest import SRPABreakoutRetest


def _ctx(features, state=None) -> EvaluationContext:
    return EvaluationContext(
        event_kind="BarsPublished",
        decision_at=datetime(2026, 1, 1),
        snapshot_id="snap",
        calendar_version="demo",
        execution_profile="EOD_ASSIST",
        state=state or {},
        position_view={},
        features=features,
    )


def test_idle_to_breakout_seen() -> None:
    strategy = SRPABreakoutRetest()
    level = type("Level", (), {"kind": "resistance", "center": 10.0, "high": 10.2, "low": 9.8, "confirmed_pivot_count": 2})()
    features = {
        "close": 10.7,
        "high": 10.8,
        "low": 10.3,
        "open": 10.2,
        "atr14": 0.5,
        "clv": 0.8,
        "body_ratio": 0.6,
        "levels": [level],
        "session_id": "S1",
        "overlap10": 0.2,
        "er20": 0.5,
        "slope20": 0.4,
        "ema20": 10.0,
        "ema60": 9.8,
    }
    result = strategy.evaluate(_ctx(features))
    assert result.next_state["setup"].state == "BREAKOUT_SEEN"


def test_no_future_high_is_used() -> None:
    strategy = SRPABreakoutRetest()
    features = {"close": 10.0, "high": 10.1, "low": 9.9, "open": 9.95, "atr14": 0.5, "clv": 0.5, "body_ratio": 0.2, "levels": [], "session_id": "S1"}
    result = strategy.evaluate(_ctx(features))
    assert result.intents == []


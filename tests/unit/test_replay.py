from __future__ import annotations

from xquant.execution.replay import run_replay
from xquant.marketdata.synthetic import generate_synthetic_bars


def test_demo_replay_generates_report() -> None:
    bars = generate_synthetic_bars("DEMO.EXAMPLE", n=180, seed=42)
    result = run_replay(bars, run_id="test-run")
    assert result.summary["demo"] is True
    assert result.summary["simulation_only"] is True
    assert len(result.events) > 0


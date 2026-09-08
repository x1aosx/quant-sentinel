from __future__ import annotations

from xquant.portfolio.risk import size_position


def test_sizing_example_matches_spec() -> None:
    result = size_position(
        equity=100_000,
        entry=10.05,
        stop=9.40,
        risk_fraction=0.0025,
        gap_buffer_atr=0.5,
        atr=0.5,
        per_share_cost=0.03,
        lot_size=100,
    )
    assert result.quantity == 200
    assert abs(result.budgeted_risk - 186.0) < 1e-6


def test_invalid_inputs_return_zero() -> None:
    result = size_position(equity=100_000, entry=10.0, stop=11.0)
    assert result.quantity == 0


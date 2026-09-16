from __future__ import annotations

from pytest import approx

from xquant.discovery import MarketRegime, dynamic_weights


def test_regime_weights_are_dynamic_and_normalized() -> None:
    bull = dynamic_weights(MarketRegime.BULL)
    bear = dynamic_weights(MarketRegime.BEAR)
    risk_off = dynamic_weights(MarketRegime.RISK_OFF)
    risk_on = dynamic_weights(MarketRegime.RISK_ON)

    assert bull.weight_for("theme") > bear.weight_for("theme")
    assert bull.weight_for("attention_momentum") > bear.weight_for(
        "attention_momentum"
    )
    assert bear.weight_for("risk") > bull.weight_for("risk")
    assert risk_off.weight_for("risk") > risk_on.weight_for("risk")
    assert sum(bull.weights.values()) == approx(1.0)
    assert sum(risk_off.weights.values()) == approx(1.0)


def test_weight_overrides_are_applied_before_normalization() -> None:
    default = dynamic_weights(MarketRegime.SIDEWAYS)
    custom = dynamic_weights(
        MarketRegime.SIDEWAYS,
        {
            "theme": 0.50,
            "risk": 0.02,
        },
    )

    assert custom.weight_for("theme") > default.weight_for("theme")
    assert custom.weight_for("risk") < default.weight_for("risk")
    assert sum(custom.weights.values()) == approx(1.0)

from __future__ import annotations

from collections.abc import Mapping

from .domain import (
    DEFAULT_SCORE_WEIGHTS,
    DiscoveryWeights,
    MarketRegime,
)

REGIME_OVERRIDES: dict[MarketRegime, dict[str, float]] = {
    MarketRegime.BULL: {
        "market_fit": 0.08,
        "theme": 0.17,
        "forward_theme": 0.13,
        "attention_momentum": 0.11,
        "capital": 0.12,
        "price_action": 0.12,
        "alpha": 0.11,
        "crowding": 0.08,
        "risk": 0.08,
    },
    MarketRegime.BEAR: {
        "theme": 0.07,
        "forward_theme": 0.07,
        "sentiment": 0.03,
        "attention_momentum": 0.04,
        "capital": 0.06,
        "price_action": 0.06,
        "alpha": 0.08,
        "fundamental": 0.14,
        "liquidity": 0.08,
        "crowding": 0.12,
        "risk": 0.25,
    },
    MarketRegime.SIDEWAYS: {
        "theme": 0.09,
        "forward_theme": 0.12,
        "policy": 0.10,
        "global_event": 0.10,
        "attention_momentum": 0.08,
        "capital": 0.09,
        "price_action": 0.12,
        "alpha": 0.10,
        "liquidity": 0.06,
        "crowding": 0.07,
        "risk": 0.07,
    },
    MarketRegime.HIGH_VOLATILITY: {
        "market_fit": 0.05,
        "theme": 0.07,
        "forward_theme": 0.08,
        "global_event": 0.08,
        "sentiment": 0.03,
        "attention_momentum": 0.04,
        "capital": 0.06,
        "price_action": 0.08,
        "alpha": 0.08,
        "fundamental": 0.10,
        "liquidity": 0.08,
        "crowding": 0.10,
        "risk": 0.15,
    },
    MarketRegime.LOW_VOLATILITY: {
        "theme": 0.13,
        "forward_theme": 0.13,
        "policy": 0.09,
        "attention_momentum": 0.09,
        "capital": 0.11,
        "price_action": 0.11,
        "alpha": 0.11,
        "fundamental": 0.08,
        "liquidity": 0.07,
        "crowding": 0.04,
        "risk": 0.04,
    },
    MarketRegime.RISK_ON: {
        "market_fit": 0.08,
        "theme": 0.17,
        "forward_theme": 0.14,
        "global_event": 0.07,
        "sentiment": 0.06,
        "attention_momentum": 0.10,
        "capital": 0.12,
        "price_action": 0.11,
        "alpha": 0.10,
        "crowding": 0.05,
        "risk": 0.05,
    },
    MarketRegime.RISK_OFF: {
        "theme": 0.06,
        "forward_theme": 0.06,
        "policy": 0.10,
        "global_event": 0.06,
        "sentiment": 0.02,
        "attention_momentum": 0.03,
        "capital": 0.05,
        "price_action": 0.05,
        "alpha": 0.07,
        "fundamental": 0.13,
        "liquidity": 0.09,
        "crowding": 0.11,
        "risk": 0.22,
    },
}


def dynamic_weights(
    regime: MarketRegime | str,
    overrides: Mapping[str, float] | None = None,
    *,
    model_version: str = "discovery-mvp-v1",
) -> DiscoveryWeights:
    """Return normalized weights for a market regime.

    Overrides are applied after the regime profile and before normalization.
    """

    market_regime = MarketRegime(regime)
    weights = dict(DEFAULT_SCORE_WEIGHTS)
    weights.update(REGIME_OVERRIDES[market_regime])
    weights.update(
        {str(key): float(value) for key, value in (overrides or {}).items()}
    )
    return DiscoveryWeights(weights=weights, model_version=model_version)


def regime_weights(
    regime: MarketRegime | str,
    overrides: Mapping[str, float] | None = None,
) -> DiscoveryWeights:
    return dynamic_weights(regime, overrides)

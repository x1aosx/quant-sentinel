from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from .domain import (
    DEFAULT_SCORE_WEIGHTS,
    PENALTY_FEATURES,
    CandidateState,
    DiscoveryCandidate,
    DiscoveryResult,
    DiscoveryWeights,
    EventSnapshot,
    MarketRegime,
    ScoreComponent,
    StockSnapshot,
    ThemeSnapshot,
    utc_now,
)

MODEL_VERSION = "discovery-mvp-v1"

FEATURE_LABELS: dict[str, str] = {
    "market_fit": "市场环境匹配",
    "theme": "当前主题热度",
    "forward_theme": "未来主题热度",
    "policy": "政策支持",
    "global_event": "全球事件影响",
    "sentiment": "市场情绪",
    "attention_momentum": "关注度动量",
    "capital": "资金流向",
    "price_action": "价格行为",
    "alpha": "Alpha 因子",
    "fundamental": "基本面",
    "liquidity": "流动性",
    "crowding": "拥挤度",
    "risk": "风险",
}


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _average(values: Sequence[float], default: float = 50.0) -> float:
    return sum(values) / len(values) if values else default


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _event_contribution(event: EventSnapshot) -> float:
    """Convert an event into a 0-100 stock impact score without using a decision threshold."""

    freshness = event.novelty / 100
    unpriced = 1 - event.priced_in / 100
    strength = event.importance / 100 * event.magnitude / 100 * freshness * unpriced
    direction = event.direction.upper()
    if direction == "POSITIVE":
        return _clamp_score(50 + strength * 50)
    if direction == "NEGATIVE":
        return _clamp_score(50 - strength * 50)
    return 50.0


def _event_risk(event: EventSnapshot) -> float:
    if event.direction.upper() != "NEGATIVE":
        return 0.0
    return _clamp_score(event.importance * event.magnitude / 100)


def _confidence_for(
    stock: StockSnapshot,
    themes: Sequence[ThemeSnapshot],
    events: Sequence[EventSnapshot],
) -> float:
    context = [stock.confidence, *(theme.confidence for theme in themes)]
    context.extend(event.confidence for event in events)
    return max(0.0, min(1.0, sum(context) / len(context)))


def weighted_discovery_score(components: Sequence[ScoreComponent]) -> float:
    return _clamp_score(sum(component.weighted_value for component in components))


def rank_candidates(
    candidates: Sequence[DiscoveryCandidate],
) -> list[DiscoveryCandidate]:
    """Rank candidates cross-sectionally with deterministic tie-breaks."""

    ranked = sorted(
        candidates,
        key=lambda item: (-item.discovery_score, -item.confidence, item.stock_id),
    )
    total = len(ranked)
    for index, candidate in enumerate(ranked, start=1):
        candidate.rank = index
        candidate.rank_percentile = index / total if total else 1.0
    return ranked


def select_top_n(
    candidates: Sequence[DiscoveryCandidate],
    top_n: int,
) -> list[DiscoveryCandidate]:
    if top_n < 0:
        raise ValueError("top_n must be non-negative")
    ranked = candidates if _is_ranked(candidates) else rank_candidates(candidates)
    return list(ranked[:top_n])


def select_top_percentile(
    candidates: Sequence[DiscoveryCandidate],
    top_percentile: float,
) -> list[DiscoveryCandidate]:
    percentile = float(top_percentile)
    if not 0 <= percentile <= 1:
        raise ValueError("top_percentile must be between 0 and 1")
    ranked = candidates if _is_ranked(candidates) else rank_candidates(candidates)
    if not ranked or percentile == 0:
        return []
    count = max(1, math.ceil(len(ranked) * percentile))
    return list(ranked[:count])


def _is_ranked(candidates: Sequence[DiscoveryCandidate]) -> bool:
    return all(candidate.rank > 0 for candidate in candidates)


class DiscoveryEngine:
    """Pure calculation engine for stock opportunity discovery."""

    def __init__(
        self,
        weights: DiscoveryWeights | None = None,
        *,
        model_version: str = MODEL_VERSION,
    ) -> None:
        self.weights = weights or DiscoveryWeights(
            weights=dict(DEFAULT_SCORE_WEIGHTS),
            model_version=model_version,
        )
        self.model_version = model_version

    def score_stock(
        self,
        stock: StockSnapshot,
        themes: Sequence[ThemeSnapshot],
        events: Sequence[EventSnapshot],
    ) -> DiscoveryCandidate:
        linked_themes = _linked_themes(stock, themes)
        linked_events = _linked_events(stock, linked_themes, events)
        values, component_reasons, component_risks = self._feature_values(
            stock, linked_themes, linked_events
        )

        components: list[ScoreComponent] = []
        for name in self.weights.weights:
            score = _clamp_score(values.get(name, 50.0))
            weight = self.weights.weight_for(name)
            penalty = name in PENALTY_FEATURES
            weighted_value = -score * weight if penalty else score * weight
            components.append(
                ScoreComponent(
                    name=name,
                    score=score,
                    weight=weight,
                    weighted_value=weighted_value,
                    reasons=component_reasons.get(
                        name,
                        [f"{FEATURE_LABELS.get(name, name)}缺少明确依据，按中性值处理"],
                    ),
                    risks=component_risks.get(
                        name,
                        [f"{FEATURE_LABELS.get(name, name)}未发现额外风险"],
                    ),
                    penalty=penalty,
                )
            )

        discovery_score = weighted_discovery_score(components)
        confidence = _confidence_for(stock, linked_themes, linked_events)
        explanation_reasons = self._candidate_reasons(
            stock, linked_themes, linked_events, components
        )
        explanation_risks = self._candidate_risks(
            stock, linked_themes, linked_events, components
        )
        return DiscoveryCandidate(
            stock_id=stock.stock_id,
            discovery_score=discovery_score,
            state=CandidateState.DISCOVERED,
            market_regime=MarketRegime.SIDEWAYS,
            theme_ids=[theme.theme_id for theme in linked_themes],
            event_ids=[event.event_id for event in linked_events],
            score_components=components,
            reasons=explanation_reasons,
            risks=explanation_risks,
            confidence=confidence,
            model_version=self.model_version,
            created_at=stock.as_of,
            updated_at=stock.as_of,
            metadata={
                "stock_name": stock.name,
                "as_of": stock.as_of.isoformat(),
                **(stock.metadata or {}),
            },
        )

    def discover(
        self,
        stocks: Sequence[StockSnapshot],
        themes: Sequence[ThemeSnapshot] = (),
        events: Sequence[EventSnapshot] = (),
        *,
        regime: MarketRegime | str = MarketRegime.SIDEWAYS,
        generated_at: datetime | None = None,
        top_n: int | None = None,
        top_percentile: float | None = None,
    ) -> DiscoveryResult:
        market_regime = MarketRegime(regime)
        ranked = self.rank(stocks, themes, events, regime=market_regime)
        if top_n is not None and top_percentile is not None:
            raise ValueError("specify either top_n or top_percentile, not both")
        if top_n is not None:
            selected = select_top_n(ranked, top_n)
        elif top_percentile is not None:
            selected = select_top_percentile(ranked, top_percentile)
        else:
            selected = list(ranked)
        selected_ids = {candidate.stock_id for candidate in selected}
        for candidate in ranked:
            candidate.selected = candidate.stock_id in selected_ids
        current = generated_at or utc_now()
        return DiscoveryResult(
            market_regime=market_regime,
            generated_at=current,
            universe_size=len(ranked),
            selected_count=len(selected),
            candidates=selected,
            rankings=ranked,
            model_version=self.model_version,
            weights=dict(self.weights.weights),
            theme_ids=[theme.theme_id for theme in themes],
            event_ids=[event.event_id for event in events],
            metadata={
                "top_n": top_n,
                "top_percentile": top_percentile,
                "ranking_method": "cross_sectional",
            },
        )

    def rank(
        self,
        stocks: Sequence[StockSnapshot],
        themes: Sequence[ThemeSnapshot] = (),
        events: Sequence[EventSnapshot] = (),
        *,
        regime: MarketRegime | str = MarketRegime.SIDEWAYS,
    ) -> list[DiscoveryCandidate]:
        market_regime = MarketRegime(regime)
        candidates = [
            replace(
                self.score_stock(stock, themes, events),
                market_regime=market_regime,
            )
            for stock in stocks
            if stock.eligible
        ]
        return rank_candidates(candidates)

    def _feature_values(
        self,
        stock: StockSnapshot,
        themes: Sequence[ThemeSnapshot],
        events: Sequence[EventSnapshot],
    ) -> tuple[dict[str, float], dict[str, list[str]], dict[str, list[str]]]:
        values = {
            name: _clamp_score(stock.scores.get(name, 50.0))
            for name in self.weights.weights
        }
        reasons = {
            name: list(stock.score_reasons.get(name, []))
            for name in self.weights.weights
        }
        risks = {
            name: list(stock.score_risks.get(name, []))
            for name in self.weights.weights
        }
        if themes:
            values["theme"] = _average([theme.current_heat_score for theme in themes])
            values["forward_theme"] = _average(
                [theme.forward_heat_score for theme in themes]
            )
            values["policy"] = _average([theme.policy_score for theme in themes])
            values["sentiment"] = _average([theme.sentiment_score for theme in themes])
            values["capital"] = _average([theme.capital_score for theme in themes])
            values["attention_momentum"] = _average(
                [theme.attention_momentum_score for theme in themes]
            )
            values["crowding"] = max(theme.crowding_score for theme in themes)
            for theme in themes:
                label = theme.name or theme.theme_id
                reasons["theme"].append(
                    f"主题 {label} 当前热度 {theme.current_heat_score:.1f}"
                )
                reasons["forward_theme"].append(
                    f"主题 {label} 未来热度 {theme.forward_heat_score:.1f}"
                )
                reasons["policy"].append(
                    f"主题 {label} 政策评分 {theme.policy_score:.1f}"
                )
                reasons["sentiment"].append(
                    f"主题 {label} 情绪评分 {theme.sentiment_score:.1f}"
                )
                reasons["capital"].append(
                    f"主题 {label} 资金评分 {theme.capital_score:.1f}"
                )
                reasons["attention_momentum"].append(
                    f"主题 {label} 关注度动量 {theme.attention_momentum_score:.1f}"
                )
                reasons["crowding"].append(
                    f"主题 {label} 拥挤度 {theme.crowding_score:.1f}"
                )
                risks["crowding"].extend(theme.risks)
                if theme.crowding_score >= 70:
                    risks["crowding"].append(
                        f"主题 {label} 拥挤度偏高：{theme.crowding_score:.1f}"
                    )
                if theme.state in {"OVERCROWDED", "DIVERGENCE", "COOLING", "DEAD"}:
                    risks["risk"].append(f"主题 {label} 当前处于 {theme.state}")
        if events:
            event_scores = [_event_contribution(event) for event in events]
            values["global_event"] = _average(event_scores)
            event_risk = max((_event_risk(event) for event in events), default=0.0)
            values["risk"] = max(values.get("risk", 50.0), event_risk)
            for event in events:
                label = event.title or event.event_id
                reasons["global_event"].append(
                    f"事件 {label}，方向 {event.direction}，影响 {event.magnitude:.1f}"
                )
                risks["risk"].extend(event.risks)
                if event.direction.upper() == "NEGATIVE":
                    risks["risk"].append(
                        f"负面事件 {label}，重要度 {event.importance:.1f}"
                    )
        for name, general_reasons in reasons.items():
            if not general_reasons:
                reasons[name] = [
                    f"{FEATURE_LABELS.get(name, name)}使用快照分 {values[name]:.1f}"
                ]
            risks[name].extend(stock.risks)
            if not risks[name]:
                risks[name] = [f"{FEATURE_LABELS.get(name, name)}未发现额外风险"]
        return values, reasons, risks

    def _candidate_reasons(
        self,
        stock: StockSnapshot,
        themes: Sequence[ThemeSnapshot],
        events: Sequence[EventSnapshot],
        components: Sequence[ScoreComponent],
    ) -> list[str]:
        reasons = list(stock.reasons)
        reasons.extend(
            f"{FEATURE_LABELS.get(component.name, component.name)} {component.score:.1f} 分，"
            f"权重 {component.weight:.3f}，贡献 {component.weighted_value:+.2f}"
            for component in sorted(
                components,
                key=lambda item: abs(item.weighted_value),
                reverse=True,
            )[:6]
        )
        for theme in themes:
            label = theme.name or theme.theme_id
            reasons.append(f"关联主题 {label}，状态 {theme.state}")
            reasons.extend(theme.reasons)
        for event in events:
            reasons.append(
                f"关联事件 {event.title or event.event_id}，方向 {event.direction}"
            )
            reasons.extend(event.reasons)
        return _unique(reasons) or ["进入横截面候选排名"]

    def _candidate_risks(
        self,
        stock: StockSnapshot,
        themes: Sequence[ThemeSnapshot],
        events: Sequence[EventSnapshot],
        components: Sequence[ScoreComponent],
    ) -> list[str]:
        risks = list(stock.risks)
        for component in components:
            if component.penalty or component.score < 50:
                risks.append(
                    f"{FEATURE_LABELS.get(component.name, component.name)} "
                    f"{component.score:.1f} 分，贡献 {component.weighted_value:+.2f}"
                )
            risks.extend(component.risks)
        for theme in themes:
            risks.extend(theme.risks)
        for event in events:
            risks.extend(event.risks)
        return _unique(risks) or ["当前未识别额外风险"]


def discover(
    stocks: Sequence[StockSnapshot],
    themes: Sequence[ThemeSnapshot] = (),
    events: Sequence[EventSnapshot] = (),
    *,
    regime: MarketRegime | str = MarketRegime.SIDEWAYS,
    weights: DiscoveryWeights | None = None,
    top_n: int | None = None,
    top_percentile: float | None = None,
) -> DiscoveryResult:
    return DiscoveryEngine(weights=weights).discover(
        stocks,
        themes,
        events,
        regime=regime,
        top_n=top_n,
        top_percentile=top_percentile,
    )


def _linked_themes(
    stock: StockSnapshot,
    themes: Sequence[ThemeSnapshot],
) -> list[ThemeSnapshot]:
    theme_ids = set(stock.theme_ids)
    return [
        theme
        for theme in themes
        if theme.theme_id in theme_ids or stock.stock_id in theme.stock_ids
    ]


def _linked_events(
    stock: StockSnapshot,
    themes: Sequence[ThemeSnapshot],
    events: Sequence[EventSnapshot],
) -> list[EventSnapshot]:
    event_ids = set(stock.event_ids)
    theme_ids = {theme.theme_id for theme in themes}
    return [
        event
        for event in events
        if event.event_id in event_ids
        or stock.stock_id in event.affected_stock_ids
        or bool(theme_ids.intersection(event.affected_theme_ids))
    ]

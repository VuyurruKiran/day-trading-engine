from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .domain import CandidateDecision, CandidateInput


@dataclass(frozen=True)
class RankingWeights:
    technical: float = 0.50
    market: float = 0.20
    news: float = 0.20
    social: float = 0.05
    fundamentals: float = 0.05

    def __post_init__(self) -> None:
        values = tuple(self.__dict__.values())
        if any(not isfinite(value) or value < 0 for value in values):
            raise ValueError("ranking weights must be finite and non-negative")
        if abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("ranking weights must sum to 1")


def context_score(
    candidate: CandidateInput, base: CandidateDecision, weights: RankingWeights
) -> float:
    if not base.eligible:
        return float("-inf")

    components = {
        "technical": base.score,
        "market": candidate.market_score,
        "news": candidate.news_score,
        "social": candidate.social_score,
        "fundamentals": candidate.fundamental_score,
    }
    technical_weight = weights.technical + sum(
        getattr(weights, name)
        for name in ("market", "news", "social", "fundamentals")
        if components[name] is None
    )
    score = base.score * technical_weight
    for name in ("market", "news", "social", "fundamentals"):
        value = components[name]
        if value is not None:
            score += value * getattr(weights, name)
    return score


def rank_all(
    rows: list[tuple[CandidateInput, CandidateDecision]],
    *,
    weights: RankingWeights | None = None,
) -> tuple[tuple[CandidateInput, CandidateDecision, float], ...]:
    weights = weights or RankingWeights()
    ranked = [
        (candidate, decision, context_score(candidate, decision, weights))
        for candidate, decision in rows
    ]
    ranked.sort(key=lambda row: (not row[1].eligible, -row[2], row[0].symbol.upper()))
    return tuple(ranked)


def shortlist(
    rows: list[tuple[CandidateInput, CandidateDecision]],
    *,
    weights: RankingWeights | None = None,
    limit: int = 5,
) -> tuple[tuple[CandidateInput, CandidateDecision, float], ...]:
    if not 2 <= limit <= 5:
        raise ValueError("V1 shortlist limit must be between 2 and 5")
    return tuple(row for row in rank_all(rows, weights=weights) if row[1].eligible)[:limit]


def ablation_scores(
    rows: list[tuple[CandidateInput, CandidateDecision]],
    *,
    weights: RankingWeights,
) -> dict[str, tuple[str, ...]]:
    variants = {
        "baseline": weights,
        "no_news": RankingWeights(
            technical=weights.technical + weights.news,
            market=weights.market,
            news=0.0,
            social=weights.social,
            fundamentals=weights.fundamentals,
        ),
        "no_social": RankingWeights(
            technical=weights.technical + weights.social,
            market=weights.market,
            news=weights.news,
            social=0.0,
            fundamentals=weights.fundamentals,
        ),
        "no_fundamentals": RankingWeights(
            technical=weights.technical + weights.fundamentals,
            market=weights.market,
            news=weights.news,
            social=weights.social,
            fundamentals=0.0,
        ),
    }
    return {
        name: tuple(candidate.symbol for candidate, _, _ in rank_all(rows, weights=variant))
        for name, variant in variants.items()
    }

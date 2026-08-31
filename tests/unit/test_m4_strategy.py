from dataclasses import replace
from math import inf, nan

import pytest

from day_trading_engine.engine.cohort import ResearchCandidate, build_research_cohort
from day_trading_engine.engine.strategy import CandidateSnapshot, StrategyPolicy, evaluate_baseline

POLICY = StrategyPolicy(
    max_spread_pct=0.01,
    max_volatility=0.05,
    min_rvol=1.0,
    min_volume=1_000,
    entry_buffer_pct=0.001,
    stop_buffer_pct=0.001,
    reward_to_risk=2.0,
)


def _snapshot(symbol: str, *, rvol: float = 2.0, bid: float = 49.9, ask: float = 50.1):
    return CandidateSnapshot(
        symbol=symbol,
        price=51.0,
        bid=bid,
        ask=ask,
        volume=100_000,
        rvol=rvol,
        volatility=0.01,
        vwap=50.0,
        opening_range_high=50.5,
        market_relative_strength=0.01,
        sector_relative_strength=0.01,
    )


def test_cohort_freezes_20_5_5_deterministically() -> None:
    candidates = [ResearchCandidate(f"S{index:02}", 100 - index) for index in range(40)]
    first = build_research_cohort(candidates, session_key="2026-08-25")
    second = build_research_cohort(list(reversed(candidates)), session_key="2026-08-25")
    assert first == second
    assert first.shortfall == 0
    assert len(first.members) == 30
    assert [member.bucket for member in first.members].count("core") == 20
    assert [member.bucket for member in first.members].count("boundary") == 5
    assert [member.bucket for member in first.members].count("diversity") == 5
    assert len({member.symbol for member in first.members}) == 30


def test_cohort_records_shortfall_without_padding_invalid_symbols() -> None:
    candidates = [ResearchCandidate(f"S{index:02}", index) for index in range(29)]
    candidates.extend(
        [
            ResearchCandidate("BAD", 999, valid=False),
            ResearchCandidate("NAN", nan),
            ResearchCandidate("INF", inf),
        ]
    )
    result = build_research_cohort(candidates, session_key="2026-08-25")
    assert len(result.members) == 29
    assert result.shortfall == 1
    assert {"BAD", "NAN", "INF"}.isdisjoint(member.symbol for member in result.members)


def test_risk_gate_can_reject_highest_scoring_symbol() -> None:
    wide_spread = _snapshot("BEST", rvol=20.0, bid=45.0, ask=55.0)
    result = evaluate_baseline(
        [wide_spread, _snapshot("AAA", rvol=3.0), _snapshot("BBB", rvol=2.0)],
        cash_usd=100.0,
        active_positions=0,
        policy=POLICY,
    )
    rejected = next(item for item in result.research if item.symbol == "BEST")
    assert rejected.eligible is False
    assert rejected.reason == "spread exceeds limit"
    assert result.primary is not None and result.primary.symbol == "AAA"
    assert len(result.finalists) == 2


def test_one_qualifier_becomes_primary_under_v31_contract() -> None:
    result = evaluate_baseline(
        [_snapshot("AAA"), _snapshot("BAD", bid=45.0, ask=55.0)],
        cash_usd=100.0,
        active_positions=0,
        policy=POLICY,
    )
    assert len(result.finalists) == 1
    assert result.primary is result.finalists[0]
    assert result.primary.symbol == "AAA"
    assert result.no_trade_reason is None


def test_contextual_rank_scores_choose_primary() -> None:
    result = evaluate_baseline(
        [_snapshot("AAA", rvol=3.0), _snapshot("BBB", rvol=2.0)],
        cash_usd=100.0,
        active_positions=0,
        policy=POLICY,
        rank_scores={"AAA": 0.1, "BBB": 0.9},
    )
    assert [plan.symbol for plan in result.finalists] == ["BBB", "AAA"]
    assert result.primary is result.finalists[0]


def test_final_score_threshold_can_produce_no_trade() -> None:
    result = evaluate_baseline(
        [_snapshot("AAA")],
        cash_usd=100.0,
        active_positions=0,
        policy=POLICY,
        rank_scores={"AAA": 0.49},
        minimum_rank_score=0.50,
    )
    assert result.finalists == ()
    assert result.primary is None
    assert result.no_trade_reason == "fewer than minimum qualifying finalists"


def test_cash_only_position_sizing_and_precise_plan() -> None:
    result = evaluate_baseline(
        [_snapshot("AAA", rvol=3.0), _snapshot("BBB", rvol=2.0)],
        cash_usd=100.0,
        active_positions=0,
        policy=POLICY,
    )
    assert result.primary is not None
    plan = result.primary
    assert plan.quantity >= 1
    assert plan.quantity * plan.entry <= 100.0
    assert plan.stop < plan.entry < plan.target
    assert plan.expiry == "END_OF_DAY"
    assert plan.status in {"WAIT", "ENTRY_VALID"}


def test_existing_position_forces_no_trade() -> None:
    result = evaluate_baseline(
        [_snapshot("AAA"), _snapshot("BBB")],
        cash_usd=100.0,
        active_positions=1,
        policy=POLICY,
    )
    assert result.primary is None
    assert result.finalists == ()
    assert result.no_trade_reason == "active V1 position already exists"


def test_non_finite_relative_strength_is_rejected() -> None:
    bad = replace(_snapshot("BAD"), market_relative_strength=nan)
    result = evaluate_baseline(
        [bad, _snapshot("AAA")], cash_usd=100.0, active_positions=0, policy=POLICY
    )
    rejected = next(item for item in result.research if item.symbol == "BAD")
    assert rejected.eligible is False
    assert rejected.reason == "non-finite market input"


@pytest.mark.parametrize("volume", [nan, inf])
def test_non_finite_volume_is_rejected(volume: float) -> None:
    bad = replace(_snapshot("BAD"), volume=volume)
    result = evaluate_baseline(
        [bad, _snapshot("AAA")], cash_usd=100.0, active_positions=0, policy=POLICY
    )
    rejected = next(item for item in result.research if item.symbol == "BAD")
    assert rejected.eligible is False
    assert rejected.reason == "non-finite market input"


@pytest.mark.parametrize("cash_usd", [nan, inf])
def test_non_finite_cash_is_rejected(cash_usd: float) -> None:
    with pytest.raises(ValueError, match="cash_usd must be positive"):
        evaluate_baseline(
            [_snapshot("AAA")],
            cash_usd=cash_usd,
            active_positions=0,
            policy=POLICY,
        )


def test_finalist_minimum_rejects_zero() -> None:
    with pytest.raises(ValueError, match="1 <= min"):
        evaluate_baseline(
            [_snapshot("AAA")],
            cash_usd=100.0,
            active_positions=0,
            policy=POLICY,
            final_min=0,
        )

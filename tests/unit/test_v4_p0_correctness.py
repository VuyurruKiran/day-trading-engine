import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from day_trading_engine.engine.domain import CandidateDecision, CandidateInput
from day_trading_engine.engine.ranking import (
    RankingWeights,
    context_score,
    score_components,
)
from day_trading_engine.engine.strategy import (
    CandidateSnapshot,
    RiskPolicy,
    StrategyPolicy,
    _position_size,
    evaluate_baseline,
    evaluate_candidate,
)
from day_trading_engine.features.market import build_market_features
from day_trading_engine.research.cycle import (
    _variant_score,
    build_ablation_report,
    classify_regimes,
    generate_monthly_report,
)

NOW = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)


def _candidate(symbol: str = "AAA") -> CandidateInput:
    return CandidateInput(
        symbol=symbol,
        as_of=NOW,
        price=10.0,
        bid=9.99,
        ask=10.01,
        volume=100_000,
        rvol=2.0,
        vwap=9.0,
        opening_range_high=9.5,
        opening_range_low=8.5,
        volatility=0.01,
        market_score=0.4,
    )


def _research_row(session: str, snapshot_id: str) -> dict[str, object]:
    return {
        "snapshot_id": snapshot_id,
        "symbol": "AAA",
        "session": session,
        "eligible": True,
        "plan": {"entry": 10.0},
        "technical_score": 0.8,
        "context": {
            "market_score": 0.4,
            "news_score": None,
            "social_score": None,
            "fundamental_score": None,
        },
    }


def test_research_full_variant_matches_live_missing_optional_semantics() -> None:
    candidate = _candidate()
    decision = CandidateDecision("AAA", True, 0.8, ("ok",))
    live = context_score(candidate, decision, RankingWeights())
    research = _variant_score(
        _research_row("2026-08-28", "snap"),
        ("technical", "market", "news", "reddit", "fundamentals"),
    )
    assert live == pytest.approx(0.72)
    assert research == pytest.approx(0.72)
    assert research == pytest.approx(live)


def test_shared_score_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        RankingWeights(
            technical=-0.1,
            market=0.3,
            news=0.5,
            social=0.1,
            fundamentals=0.2,
        )
    with pytest.raises(ValueError, match="sum to 1"):
        RankingWeights(technical=0.4)
    with pytest.raises(ValueError, match="critical market"):
        score_components(
            technical=0.5,
            market=None,
            news=None,
            social=None,
            fundamentals=None,
            weights=RankingWeights(),
        )
    with pytest.raises(ValueError, match="normalized"):
        score_components(
            technical=1.1,
            market=0.5,
            news=None,
            social=None,
            fundamentals=None,
            weights=RankingWeights(),
        )


def test_unknown_outcome_does_not_dilute_known_expectancy() -> None:
    candidates = [
        _research_row("2026-08-27", "known"),
        _research_row("2026-08-28", "ambiguous"),
    ]
    outcomes = [
        {
            "snapshot_id": "known",
            "symbol": "AAA",
            "status": "complete",
            "fidelity": "BAR_ONLY",
            "entry_triggered": True,
            "outcome": "stop_before_target",
            "target_before_stop": False,
            "shadow_return": -0.10,
        },
        {
            "snapshot_id": "ambiguous",
            "symbol": "AAA",
            "status": "complete",
            "fidelity": "BAR_ONLY",
            "entry_triggered": True,
            "outcome": "ambiguous_same_bar",
            "target_before_stop": False,
            "shadow_return": None,
        },
    ]
    full = next(
        row
        for row in build_ablation_report(candidates, outcomes)
        if row["variant"] == "E_FULL"
    )
    assert full["count"] == 1
    assert full["selected"] == 2
    assert full["unknown"] == 1
    assert full["unknown_rate"] == pytest.approx(0.5)
    assert full["expectancy"] == pytest.approx(-0.10)


def test_monthly_report_counts_missing_candidate_outcome_as_unknown(tmp_path) -> None:
    directory = tmp_path / "data" / "research" / "2026" / "08"
    directory.mkdir(parents=True)
    candidate = _research_row("2026-08-28", "snap")
    pd.DataFrame(
        [
            {
                "snapshot_id": "snap",
                "symbol": "AAA",
                "payload": json.dumps(candidate),
            }
        ]
    ).to_parquet(directory / "snap.candidates.parquet", index=False)

    report = json.loads(generate_monthly_report(tmp_path, "2026-08").read_text(encoding="utf-8"))
    assert report["data_quality"]["candidate_rows"] == 1
    assert report["data_quality"]["outcome_rows"] == 0
    assert report["data_quality"]["unknown_outcomes"] == 1


def test_filing_regime_uses_production_evidence_vocabulary() -> None:
    row = {
        "eligible": True,
        "features": {"spread_pct": 0.001},
        "context": {
            "news_score": 0.5,
            "social_score": 0.5,
            "fundamental_score": 0.5,
            "evidence_counts": {"fundamentals": 1, "news": 0, "macro": 0},
        },
    }
    regime = classify_regimes(row)
    assert regime["version"] == "regime-v2"
    assert regime["catalyst"] == "FILING"


def _market_samples(times: list[str]) -> pd.DataFrame:
    size = len(times)
    return pd.DataFrame(
        {
            "received_at": times,
            "last_trade_price": [10.0 + index * 0.01 for index in range(size)],
            "volume": [100 + index * 10 for index in range(size)],
            "bid_price": [9.99 + index * 0.01 for index in range(size)],
            "ask_price": [10.01 + index * 0.01 for index in range(size)],
        }
    )


@pytest.mark.parametrize(
    "times, error",
    [
        (
            [
                "2026-08-28T13:30:00Z",
                "2026-08-28T13:30:30Z",
                "2026-08-28T13:32:00Z",
                "2026-08-28T13:33:00Z",
                "2026-08-28T13:34:00Z",
                "2026-08-28T13:35:00Z",
            ],
            "every expected minute",
        ),
        (
            [
                "2026-08-28T13:30:00Z",
                "2026-08-28T13:31:00Z",
                "2026-08-28T13:33:00Z",
                "2026-08-28T13:34:00Z",
                "2026-08-28T13:35:00Z",
            ],
            "every expected minute",
        ),
        (
            [
                "2026-08-28T13:31:00Z",
                "2026-08-28T13:30:00Z",
                "2026-08-28T13:32:00Z",
                "2026-08-28T13:33:00Z",
                "2026-08-28T13:34:00Z",
                "2026-08-28T13:35:00Z",
            ],
            "unique and chronological",
        ),
    ],
)
def test_opening_range_rejects_clustered_gapped_and_out_of_order_evidence(
    times: list[str], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        build_market_features(
            _market_samples(times),
            as_of=datetime(2026, 8, 28, 13, 35, tzinfo=UTC),
        )


def test_opening_range_ignores_ineligible_other_session_rows() -> None:
    frame = _market_samples(
        [
            "2026-08-28T13:30:00Z",
            "2026-08-28T13:31:00Z",
            "2026-08-28T13:32:00Z",
            "2026-08-28T13:33:00Z",
            "2026-08-28T13:34:00Z",
            "2026-08-28T13:35:00Z",
        ]
    )
    frame["is_trade_eligible"] = True
    stale = frame.iloc[[0]].copy()
    stale["received_at"] = "2026-08-27T13:30:00Z"
    stale["is_trade_eligible"] = False

    features = build_market_features(
        pd.concat([stale, frame], ignore_index=True),
        as_of=datetime(2026, 8, 28, 13, 35, tzinfo=UTC),
    )
    assert features["opening_range_high"].iloc[-1] == pytest.approx(10.04)


def test_opening_range_uses_source_timestamp_not_delayed_receipt() -> None:
    frame = _market_samples(
        [
            "2026-08-28T13:31:01Z",
            "2026-08-28T13:32:01Z",
            "2026-08-28T13:33:01Z",
            "2026-08-28T13:34:01Z",
            "2026-08-28T13:35:01Z",
            "2026-08-28T13:36:01Z",
        ]
    )
    frame["source_at"] = [
        "2026-08-28T13:30:59Z",
        "2026-08-28T13:31:59Z",
        "2026-08-28T13:32:59Z",
        "2026-08-28T13:33:59Z",
        "2026-08-28T13:34:59Z",
        "2026-08-28T13:35:59Z",
    ]

    features = build_market_features(
        frame,
        as_of=datetime(2026, 8, 28, 13, 36, 5, tzinfo=UTC),
    )
    assert features["opening_range_high"].iloc[-1] == pytest.approx(10.04)


def test_opening_range_ignores_post_open_source_timestamp_disorder() -> None:
    frame = _market_samples(
        [
            "2026-08-28T13:30:00Z",
            "2026-08-28T13:31:00Z",
            "2026-08-28T13:32:00Z",
            "2026-08-28T13:33:00Z",
            "2026-08-28T13:34:00Z",
            "2026-08-28T13:35:00Z",
            "2026-08-28T13:36:00Z",
            "2026-08-28T13:37:00Z",
        ]
    )
    frame["source_at"] = [
        "2026-08-28T13:30:00Z",
        "2026-08-28T13:31:00Z",
        "2026-08-28T13:32:00Z",
        "2026-08-28T13:33:00Z",
        "2026-08-28T13:34:00Z",
        "2026-08-28T13:36:00Z",
        "2026-08-28T13:35:00Z",
        "2026-08-28T13:35:00Z",
    ]

    features = build_market_features(
        frame,
        as_of=datetime(2026, 8, 28, 13, 37, tzinfo=UTC),
    )
    assert features["opening_range_high"].iloc[-1] == pytest.approx(10.04)


def test_opening_range_is_derived_from_regular_open_not_first_sample() -> None:
    times = [
        "2026-08-28T08:00:00Z",
        "2026-08-28T13:30:00Z",
        "2026-08-28T13:31:00Z",
        "2026-08-28T13:32:00Z",
        "2026-08-28T13:33:00Z",
        "2026-08-28T13:34:00Z",
        "2026-08-28T13:35:00Z",
    ]
    frame = pd.DataFrame(
        {
            "received_at": times,
            "source_at": times,
            "last_trade_price": [50.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
            "volume": [100, 200, 300, 400, 500, 600, 700],
            "bid_price": [49.9, 9.9, 10.9, 11.9, 12.9, 13.9, 14.9],
            "ask_price": [50.1, 10.1, 11.1, 12.1, 13.1, 14.1, 15.1],
        }
    )

    features = build_market_features(
        frame,
        as_of=datetime(2026, 8, 28, 13, 35, tzinfo=UTC),
    )
    assert features["opening_range_high"].iloc[-1] == pytest.approx(14.0)
    assert features["opening_range_low"].iloc[-1] == pytest.approx(10.0)


def test_policy_positional_order_remains_backward_compatible() -> None:
    strategy = StrategyPolicy(0.01, 0.05, 1.0, 1, 0.0, 0.0, 2.0, 0.35)
    assert strategy.extended_score_share == pytest.approx(0.35)
    assert strategy.max_risk_usd == pytest.approx(1.0)

    risk = RiskPolicy(0.01, 0.08, 100_000, 1.0, 2.5, 2.0, 30)
    assert risk.max_risk_usd == pytest.approx(2.5)
    assert risk.reward_risk == pytest.approx(2.0)
    assert risk.setup_minutes == 30


def test_position_size_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="finite"):
        _position_size(cash=float("nan"), entry=10, stop=9, max_risk_usd=1)
    with pytest.raises(ValueError, match="positive"):
        _position_size(cash=0, entry=10, stop=9, max_risk_usd=1)
    assert _position_size(cash=100, entry=10, stop=10, max_risk_usd=1) == 0


def test_live_and_legacy_planners_share_max_loss_sizing() -> None:
    risk_policy = RiskPolicy(min_volume=1, max_risk_usd=3.0)
    decision = evaluate_candidate(_candidate(), cash=100.0, policy=risk_policy)
    assert decision.plan is not None
    assert decision.plan.quantity == _position_size(
        cash=100.0,
        entry=decision.plan.entry,
        stop=decision.plan.stop,
        max_risk_usd=3.0,
    )
    assert decision.plan.max_loss <= 3.0

    strategy_policy = StrategyPolicy(
        max_spread_pct=0.01,
        max_volatility=0.05,
        min_rvol=1.0,
        min_volume=1,
        entry_buffer_pct=0.0,
        stop_buffer_pct=0.0,
        reward_to_risk=2.0,
        max_risk_usd=3.0,
        extended_score_share=0.0,
    )
    snapshot = CandidateSnapshot(
        symbol="AAA",
        price=10.0,
        bid=9.99,
        ask=10.01,
        volume=100_000,
        rvol=2.0,
        volatility=0.01,
        vwap=9.0,
        opening_range_high=9.5,
    )
    result = evaluate_baseline(
        [snapshot], cash_usd=100.0, active_positions=0, policy=strategy_policy
    )
    assert result.primary is not None
    assert result.primary.quantity == _position_size(
        cash=100.0,
        entry=result.primary.entry,
        stop=result.primary.stop,
        max_risk_usd=3.0,
    )
    assert result.primary.quantity * (result.primary.entry - result.primary.stop) <= 3.0


def test_serialized_plan_preserves_exact_low_price_risk_geometry() -> None:
    policy = StrategyPolicy(
        max_spread_pct=0.02,
        max_volatility=0.05,
        min_rvol=1.0,
        min_volume=1,
        entry_buffer_pct=0.0,
        stop_buffer_pct=0.0,
        reward_to_risk=2.0,
        max_risk_usd=1.0,
        extended_score_share=0.0,
    )
    snapshot = CandidateSnapshot(
        symbol="LOW",
        price=0.1,
        bid=0.0999,
        ask=0.1,
        volume=100_000,
        rvol=2.0,
        volatility=0.01,
        vwap=0.09849,
        opening_range_high=0.1,
    )
    result = evaluate_baseline([snapshot], cash_usd=100.0, active_positions=0, policy=policy)
    assert result.primary is not None
    assert result.primary.stop == pytest.approx(0.09849)
    exposure = result.primary.quantity * (result.primary.entry - result.primary.stop)
    assert exposure <= 1.0

from datetime import UTC, datetime, timedelta, timezone
from math import nan

import pytest

from day_trading_engine.ai.interpretation import (
    ClassificationCache,
    Direction,
    EventClassifier,
    classify_cached,
)
from day_trading_engine.engine.domain import CandidateInput
from day_trading_engine.engine.ranking import RankingWeights, context_score, shortlist
from day_trading_engine.engine.strategy import RiskPolicy, evaluate_candidate
from day_trading_engine.paper.ledger import PaperLedger
from day_trading_engine.paper.replay import ReplayBar, apply_actual_trade, evaluate_plan
from day_trading_engine.research.realism import (
    ExecutionProfile,
    PriceObservation,
    manual_fill,
    round_trip_cost,
)
from day_trading_engine.research.refinement import ChallengerResult, ChampionCycle
from day_trading_engine.research.validation import HoldoutRegistry
from day_trading_engine.ui.state import ReportStore, SavedReport


def candidate(**changes):
    """Build a valid candidate with optional field overrides for regressions."""
    data = dict(
        symbol="ABC",
        as_of=datetime(2026, 8, 25, 14, 0, tzinfo=UTC),
        price=10.0,
        bid=9.99,
        ask=10.01,
        volume=1_000_000.0,
        rvol=2.0,
        vwap=9.5,
        opening_range_high=9.9,
        opening_range_low=9.0,
        volatility=0.02,
        market_score=0.4,
    )
    data.update(changes)
    return CandidateInput(**data)


@pytest.mark.parametrize("field", ["volatility", "volume", "rvol", "bid", "ask"])
def test_nonfinite_market_data_fails_closed(field):
    """Reject non-finite values at the candidate trust boundary."""
    with pytest.raises(ValueError):
        candidate(**{field: nan})


def test_negative_volatility_and_bad_risk_policy_fail_closed():
    """Reject invalid volatility and risk-policy inputs."""
    with pytest.raises(ValueError):
        candidate(volatility=-0.01)
    with pytest.raises(ValueError):
        RiskPolicy(reward_risk=-2)


def test_ranking_applies_market_once_and_limit_is_locked():
    """Apply market weight once and enforce the frozen 1-5 shortlist bounds."""
    row = candidate(market_score=1.0)
    decision = evaluate_candidate(row, cash=100)
    weights = RankingWeights(technical=0.5, market=0.5, news=0, social=0, fundamentals=0)
    assert context_score(row, decision, weights) == pytest.approx(0.5 * decision.score + 0.5)
    assert len(shortlist([(row, decision)], limit=1)) == 1
    with pytest.raises(ValueError):
        shortlist([(row, decision)], limit=0)
    with pytest.raises(ValueError):
        shortlist([(row, decision)], limit=6)


def test_classifier_bad_shape_transient_failure_and_retry():
    """Keep classifier failures uncertain and retry transient failures."""
    class BadShape(EventClassifier):
        def classify(self, text):
            return None

    class Flaky(EventClassifier):
        calls = 0

        def classify(self, text):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("down")
            return {"direction": "positive", "confidence": 0.8, "impact": 0.4}

    cache = ClassificationCache()
    bad = classify_cached("x", classifier=BadShape(), cache=cache, model="m", prompt_version="p")
    flaky = Flaky()
    first = classify_cached("y", classifier=flaky, cache=cache, model="m", prompt_version="p")
    second = classify_cached("y", classifier=flaky, cache=cache, model="m", prompt_version="p")
    assert bad.direction == Direction.UNCERTAIN
    assert first.direction == Direction.UNCERTAIN
    assert second.direction == Direction.POSITIVE and flaky.calls == 2


def test_replay_validates_order_range_and_actual_exit():
    plan = evaluate_candidate(candidate(), cash=100).plan
    assert plan is not None
    hit = ReplayBar(
        plan.valid_from + timedelta(minutes=1),
        plan.target + 0.1,
        plan.entry,
        plan.target,
    )
    later = ReplayBar(
        plan.valid_from + timedelta(minutes=2),
        plan.target + 0.2,
        plan.entry,
        plan.target,
    )
    with pytest.raises(ValueError):
        evaluate_plan(plan, [later, hit])
    with pytest.raises(ValueError):
        ReplayBar(hit.ts, 10, 9, 11)
    ledger = PaperLedger()
    apply_actual_trade(ledger, plan, [hit, later])
    assert ledger.fills[-1].ts == hit.ts


def test_ledger_rejects_fractional_quantity():
    with pytest.raises(ValueError):
        PaperLedger().buy("ABC", 1.5, 10, datetime.now(UTC))


def test_realism_models_fx_slippage_and_manual_latency():
    with pytest.raises(ValueError):
        ExecutionProfile(slippage_bps=10_000)
    plain = round_trip_cost(ExecutionProfile(), entry=10, exit=11, quantity=2)
    fx = round_trip_cost(
        ExecutionProfile(fx_rate=1.3, fx_fee_bps=20), entry=10, exit=11, quantity=2
    )
    assert fx > plain
    signal = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)
    profile = ExecutionProfile(manual_latency_seconds=30)
    observations = [
        PriceObservation(signal + timedelta(seconds=10), 10),
        PriceObservation(signal + timedelta(seconds=40), 10.1),
    ]
    fill = manual_fill(profile, signal_at=signal, observations=observations, side="buy")
    assert fill is not None and fill.ts == observations[1].ts


def test_holdout_is_persistent_and_normalized(tmp_path):
    path = tmp_path / "holdout.db"
    HoldoutRegistry(path).consume(" 2026-08 ")
    with pytest.raises(ValueError):
        HoldoutRegistry(path).consume("2026-08")


def test_promotion_metrics_and_cycle_guard():
    cycle = ChampionCycle("champ", "2026-08")
    with pytest.raises(ValueError):
        cycle.consider(
            ChallengerResult("bad", nan, 0.05, True, True),
            champion_metric=0.1,
            max_drawdown_limit=0.1,
        )
    challenger = ChallengerResult("c1", 0.2, 0.05, True, True)
    assert cycle.consider(challenger, champion_metric=0.1, max_drawdown_limit=0.1) == "PROMOTED"
    with pytest.raises(ValueError):
        cycle.complete_forward_cycle("2026-08")


def test_latest_report_orders_by_utc_instant(tmp_path):
    store = ReportStore(tmp_path / "state.db")
    early = datetime(2026, 8, 25, 10, 30, tzinfo=timezone(timedelta(hours=1)))
    late = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    store.save_once(SavedReport("early", early, None, {}))
    store.save_once(SavedReport("late", late, None, {}))
    assert store.latest().snapshot_id == "late"

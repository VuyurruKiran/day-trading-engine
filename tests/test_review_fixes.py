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
from day_trading_engine.engine.ranking import RankingWeights, context_score
from day_trading_engine.engine.strategy import evaluate_candidate
from day_trading_engine.paper.ledger import PaperLedger
from day_trading_engine.paper.replay import ReplayBar, apply_actual_trade, evaluate_plan
from day_trading_engine.research.realism import ExecutionProfile, round_trip_cost
from day_trading_engine.research.refinement import ChallengerResult, ChampionCycle
from day_trading_engine.research.validation import HoldoutRegistry
from day_trading_engine.ui.state import ReportStore, SavedReport


def candidate(**changes):
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
    with pytest.raises(ValueError):
        candidate(**{field: nan})


def test_ranking_applies_market_once():
    row = candidate(market_score=1.0)
    decision = evaluate_candidate(row, cash=100)
    weights = RankingWeights(technical=0.5, market=0.5, news=0, social=0, fundamentals=0)
    assert context_score(row, decision, weights) == pytest.approx(0.5 * decision.score + 0.5)


def test_invalid_sector_shape_and_connection_failure_degrade():
    class BadShape(EventClassifier):
        def classify(self, text):
            return {"affected_sectors": "technology"}

    class Down(EventClassifier):
        def classify(self, text):
            raise ConnectionError("down")

    cache = ClassificationCache()
    first = classify_cached("x", classifier=BadShape(), cache=cache, model="m", prompt_version="p")
    second = classify_cached("y", classifier=Down(), cache=cache, model="m", prompt_version="p")
    assert first.direction == Direction.UNCERTAIN
    assert second.direction == Direction.UNCERTAIN


def test_replay_ignores_pre_plan_and_same_bar_entry_stop_is_ambiguous():
    plan = evaluate_candidate(candidate(), cash=100).plan
    assert plan is not None
    pre = ReplayBar(
        plan.valid_from - timedelta(minutes=1), plan.entry + 1, plan.entry, plan.entry
    )
    post = ReplayBar(
        plan.valid_from + timedelta(minutes=1), plan.entry - 0.01, plan.stop + 0.1, plan.entry
    )
    assert evaluate_plan(plan, [pre, post]).triggered is False
    ambiguous = ReplayBar(plan.valid_from, plan.entry + 0.1, plan.stop - 0.1, plan.entry)
    assert evaluate_plan(plan, [ambiguous]).outcome == "ambiguous_same_bar"


def test_exit_fill_uses_actual_exit_bar_and_invalid_sell_rejected():
    plan = evaluate_candidate(candidate(), cash=100).plan
    assert plan is not None
    hit = ReplayBar(
        plan.valid_from + timedelta(minutes=1), plan.target + 0.1, plan.entry, plan.target
    )
    later = ReplayBar(
        plan.valid_from + timedelta(minutes=2), plan.target + 0.2, plan.entry, plan.target
    )
    ledger = PaperLedger()
    apply_actual_trade(ledger, plan, [hit, later])
    assert ledger.fills[-1].ts == hit.ts
    ledger = PaperLedger()
    ledger.buy("ABC", 1, 10, plan.valid_from)
    with pytest.raises(ValueError):
        ledger.sell("ABC", 0, plan.valid_from)


def test_fx_cost_and_cycle_guard():
    plain = round_trip_cost(ExecutionProfile(), entry=10, exit=11, quantity=2)
    fx = round_trip_cost(
        ExecutionProfile(fx_rate=1.3, fx_fee_bps=20), entry=10, exit=11, quantity=2
    )
    assert fx > plain
    cycle = ChampionCycle("champ", "2026-08")
    challenger = ChallengerResult("c1", 0.2, 0.05, True, True)
    assert cycle.consider(challenger, champion_metric=0.1, max_drawdown_limit=0.1) == "PROMOTED"
    with pytest.raises(ValueError):
        cycle.complete_forward_cycle("2026-08")


def test_holdout_is_persistent(tmp_path):
    path = tmp_path / "holdout.db"
    HoldoutRegistry(path).consume("2026-08")
    with pytest.raises(ValueError):
        HoldoutRegistry(path).consume("2026-08")


def test_latest_report_orders_by_utc_instant(tmp_path):
    store = ReportStore(tmp_path / "state.db")
    early = datetime(2026, 8, 25, 10, 30, tzinfo=timezone(timedelta(hours=1)))
    late = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    store.save_once(SavedReport("early", early, None, {}))
    store.save_once(SavedReport("late", late, None, {}))
    assert store.latest().snapshot_id == "late"

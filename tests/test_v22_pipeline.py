from datetime import UTC, datetime

import pytest

from day_trading_engine.ai.interpretation import Direction, uncertain_event
from day_trading_engine.engine.cohort import build_research_cohort
from day_trading_engine.engine.domain import CandidateInput, CohortBucket
from day_trading_engine.engine.ranking import shortlist
from day_trading_engine.engine.strategy import evaluate_candidate
from day_trading_engine.paper.ledger import PaperLedger
from day_trading_engine.paper.replay import ReplayBar, evaluate_plan
from day_trading_engine.research.refinement import ChallengerResult, ChampionCycle
from day_trading_engine.research.validation import (
    HoldoutRegistry,
    SessionResult,
    build_evidence_report,
)


def candidate(i: int) -> CandidateInput:
    return CandidateInput(
        symbol=f"S{i:02d}",
        as_of=datetime(2026, 8, 25, 14, 0, tzinfo=UTC),
        price=10 + i / 100,
        bid=9.99 + i / 100,
        ask=10.01 + i / 100,
        volume=1_000_000,
        rvol=2.0,
        vwap=9.5,
        opening_range_high=9.9,
        opening_range_low=9.0,
        volatility=0.02,
        market_score=0.4,
    )


def test_cohort_is_20_5_5_and_deterministic():
    rows = [candidate(i) for i in range(40)]
    first = build_research_cohort(rows, session_key="2026-08-25")
    second = build_research_cohort(rows, session_key="2026-08-25")
    assert first == second and len(first) == 30
    assert sum(x.bucket == CohortBucket.CORE for x in first) == 20
    assert sum(x.bucket == CohortBucket.BOUNDARY for x in first) == 5
    assert sum(x.bucket == CohortBucket.DIVERSITY for x in first) == 5
    assert len({x.candidate.symbol for x in first}) == 30


def test_risk_blocks_bad_data_and_strategy_builds_cash_only_plan():
    good = candidate(1)
    decision = evaluate_candidate(good, cash=100)
    assert decision.eligible and decision.plan and decision.plan.quantity >= 1
    bad = CandidateInput(**{**good.__dict__, "symbol": "BAD", "stale": True})
    assert not evaluate_candidate(bad, cash=100).eligible


def test_shortlist_never_makes_ineligible_candidate_eligible():
    good, bad = candidate(1), candidate(2)
    good_decision = evaluate_candidate(good, cash=100)
    bad = CandidateInput(**{**bad.__dict__, "halted": True})
    bad_decision = evaluate_candidate(bad, cash=100)
    result = shortlist([(bad, bad_decision), (good, good_decision)])
    assert [row[0].symbol for row in result] == [good.symbol]


def test_shadow_replay_is_ledger_neutral_and_actual_ledger_reconstructs():
    plan = evaluate_candidate(candidate(1), cash=100).plan
    assert plan is not None
    bars = [ReplayBar(plan.expires_at, plan.target + 0.1, plan.entry, plan.target)]
    outcome = evaluate_plan(plan, bars)
    assert outcome.triggered
    ledger = PaperLedger()
    before = ledger.cash
    evaluate_plan(plan, bars)
    assert ledger.cash == before
    ledger.buy(plan.symbol, 1, plan.entry, bars[0].ts)
    ledger.sell(plan.symbol, plan.target, bars[0].ts)
    assert ledger.cash == pytest.approx(ledger.reconstruct_cash())


def test_ai_unavailable_is_uncertain_not_fabricated():
    event = uncertain_event("headline", model="test", prompt_version="v1")
    assert event.direction == Direction.UNCERTAIN and event.confidence == 0


def test_monthly_evidence_and_consumed_holdout_rules(tmp_path):
    report = build_evidence_report(
        [SessionResult(str(i), 30, 2, 1, (0.01,)) for i in range(15)]
    )
    assert report.candidate_rows == 450 and report.eligible_for_promotion_review
    registry = HoldoutRegistry(tmp_path / "holdouts.db")
    registry.consume("2026-08")
    with pytest.raises(ValueError):
        registry.consume("2026-08")


def test_only_one_promotion_per_cycle():
    cycle = ChampionCycle("champion", "2026-08")
    challenger = ChallengerResult("c1", 0.2, 0.05, True, True)
    assert cycle.consider(challenger, champion_metric=0.1, max_drawdown_limit=0.1) == "PROMOTED"
    assert cycle.consider(challenger, champion_metric=0.1, max_drawdown_limit=0.1) == "NO CHANGE"
    cycle.complete_forward_cycle("2026-09")
    assert cycle.consider(challenger, champion_metric=0.3, max_drawdown_limit=0.1) == "NO CHANGE"


def test_report_store_is_immutable_and_transition_requires_reason(tmp_path):
    import sqlite3

    from day_trading_engine.engine.domain import DecisionStatus
    from day_trading_engine.ui.state import ReportStore, SavedReport

    store = ReportStore(tmp_path / "state.db")
    report = SavedReport("snap-1", datetime.now(UTC), "S01", {"rank": 1})
    store.save_once(report)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_once(report)
    assert store.load("snap-1").payload == {"rank": 1}
    store.append_transition(
        "snap-1", at=datetime.now(UTC), status=DecisionStatus.WAIT, reason="entry not triggered"
    )
    with pytest.raises(ValueError):
        store.append_transition(
            "snap-1", at=datetime.now(UTC), status=DecisionStatus.WAIT, reason=""
        )


def test_canadian_activation_requires_all_market_gates():
    from day_trading_engine.research.realism import MarketActivation

    assert not MarketActivation("CA", True, False, True).enabled
    assert MarketActivation("CA", True, True, True).enabled


def test_structured_ai_mapping_validates_bounds():
    from day_trading_engine.ai.interpretation import StructuredEvent

    event = StructuredEvent.from_mapping(
        {
            "entity": "S01",
            "event_type": "guidance_cut",
            "direction": "negative",
            "impact": 0.8,
            "confidence": 0.9,
            "affected_sectors": ["technology"],
        },
        source_text="Company cuts guidance",
        model="test-model",
        prompt_version="v1",
    )
    assert event.entity == "S01" and event.direction == Direction.NEGATIVE
    with pytest.raises(ValueError):
        StructuredEvent.from_mapping(
            {"impact": 2.0},
            source_text="bad",
            model="test-model",
            prompt_version="v1",
        )


def test_strategy_registry_round_trip(tmp_path):
    from day_trading_engine.research.strategy_registry import StrategyEvidence, StrategyRegistry

    registry = StrategyRegistry(tmp_path / "research.db")
    evidence = StrategyEvidence(
        strategy_id="orb_vwap_v1",
        source="internal",
        license=None,
        hypothesis="continuation after opening range confirmation",
        required_data=("1m", "quote"),
        parameters={"rr": 2.0},
        anti_leakage_review="pass",
        reproduction_status="candidate",
        out_of_sample_metrics={"expectancy": 0.01},
        sensitivity="stable",
    )
    registry.put(evidence)
    assert registry.get(evidence.strategy_id) == evidence
    with pytest.raises(KeyError):
        registry.get("missing")


def test_actual_replay_trade_changes_ledger_only_for_primary():
    from day_trading_engine.paper.replay import apply_actual_trade

    plan = evaluate_candidate(candidate(1), cash=100).plan
    assert plan is not None
    bars = [ReplayBar(plan.expires_at, plan.target + 0.1, plan.entry, plan.target)]
    ledger = PaperLedger()
    apply_actual_trade(ledger, plan, bars)
    assert len(ledger.fills) == 2 and ledger.position_symbol is None


def test_report_store_latest_and_transition_history(tmp_path):
    from day_trading_engine.engine.domain import DecisionStatus
    from day_trading_engine.ui.state import ReportStore, SavedReport

    store = ReportStore(tmp_path / "state.db")
    created = datetime.now(UTC)
    store.save_once(SavedReport("snap-2", created, None, {"no_trade": True}))
    store.append_transition(
        "snap-2", at=created, status=DecisionStatus.NO_TRADE, reason="risk veto"
    )
    assert store.latest().snapshot_id == "snap-2"
    assert store.transitions("snap-2")[0][1] == "NO TRADE"


def test_ai_cache_avoids_reprocessing_and_timeout_is_uncertain():
    from day_trading_engine.ai.interpretation import (
        ClassificationCache,
        Direction,
        EventClassifier,
        classify_cached,
    )

    class Classifier(EventClassifier):
        calls = 0

        def classify(self, text: str) -> dict[str, object]:
            self.calls += 1
            return {"direction": "positive", "confidence": 0.8, "impact": 0.4}

    classifier = Classifier()
    cache = ClassificationCache()
    first = classify_cached(
        "headline", classifier=classifier, cache=cache, model="m", prompt_version="p1"
    )
    second = classify_cached(
        "headline", classifier=classifier, cache=cache, model="m", prompt_version="p1"
    )
    assert first == second and classifier.calls == 1 and first.direction == Direction.POSITIVE

    class TimeoutClassifier(EventClassifier):
        def classify(self, text: str) -> dict[str, object]:
            raise TimeoutError

    event = classify_cached(
        "other",
        classifier=TimeoutClassifier(),
        cache=cache,
        model="m",
        prompt_version="p1",
    )
    assert event.direction == Direction.UNCERTAIN


def test_replay_session_shadow_rows_do_not_change_ledger():
    from day_trading_engine.paper.replay import replay_session

    one = evaluate_candidate(candidate(1), cash=100).plan
    two = evaluate_candidate(candidate(2), cash=100).plan
    assert one is not None and two is not None
    bars = {
        one.symbol: [ReplayBar(one.expires_at, one.target + 0.1, one.entry, one.target)],
        two.symbol: [ReplayBar(two.expires_at, two.target + 0.1, two.entry, two.target)],
    }
    ledger = PaperLedger()
    replay_session(
        {one.symbol: one, two.symbol: two},
        bars,
        primary_symbol=one.symbol,
        ledger=ledger,
    )
    assert len(ledger.fills) == 2
    assert {fill.symbol for fill in ledger.fills} == {one.symbol}


def test_ablation_and_validation_helpers():
    from day_trading_engine.engine.ranking import RankingWeights, ablation_scores
    from day_trading_engine.research.validation import hit_rate, max_drawdown

    rows = [(candidate(i), evaluate_candidate(candidate(i), cash=100)) for i in range(1, 4)]
    variants = ablation_scores(rows, weights=RankingWeights())
    assert set(variants) == {"baseline", "no_news", "no_social", "no_fundamentals"}
    assert hit_rate([0.1, -0.1, 0.2]) == pytest.approx(2 / 3)
    assert max_drawdown([0.1, -0.1]) > 0


def test_experiment_log_and_realism_costs():
    from day_trading_engine.research.realism import ExecutionProfile, round_trip_cost
    from day_trading_engine.research.refinement import ExperimentLog

    log = ExperimentLog()
    log.add("c1", "reduce spread exposure", "rejected")
    assert log.records[0].result == "rejected"
    profile = ExecutionProfile(commission_per_order=0.1, slippage_bps=10, fill_ratio=0.5)
    assert profile.filled_quantity(3) == 1
    assert round_trip_cost(profile, entry=10, exit=11, quantity=2) > 0.2


def test_ambiguous_bar_does_not_mutate_ledger():
    from day_trading_engine.paper.replay import apply_actual_trade

    plan = evaluate_candidate(candidate(1), cash=100).plan
    assert plan is not None
    bars = [ReplayBar(plan.expires_at, plan.target + 0.1, plan.stop - 0.1, plan.entry)]
    ledger = PaperLedger()
    with pytest.raises(ValueError, match="higher-fidelity"):
        apply_actual_trade(ledger, plan, bars)
    assert ledger.cash == 100.0 and not ledger.fills and ledger.position_symbol is None


def test_full_rank_and_ablation_preserve_all_research_rows():
    from day_trading_engine.engine.ranking import RankingWeights, ablation_scores, rank_all

    rows = [(candidate(i), evaluate_candidate(candidate(i), cash=100)) for i in range(1, 9)]
    ranked = rank_all(rows)
    assert len(ranked) == 8
    variants = ablation_scores(rows, weights=RankingWeights())
    assert all(len(symbols) == 8 for symbols in variants.values())


def test_report_store_records_manual_execution_timestamps(tmp_path):
    from day_trading_engine.ui.state import ReportStore, SavedReport

    store = ReportStore(tmp_path / "state.db")
    created = datetime.now(UTC)
    store.save_once(SavedReport("snap-exec", created, "S01", {}))
    store.record_execution("snap-exec", kind="entry", at=created, price=10.25)
    assert store.execution_events("snap-exec") == (("entry", created.isoformat(), 10.25),)
    with pytest.raises(ValueError):
        store.record_execution("snap-exec", kind="bad", at=created, price=10.25)


def test_paper_ledger_cannot_start_with_external_top_up():
    with pytest.raises(ValueError, match="exactly USD 100"):
        PaperLedger(cash=101.0)


def test_decision_snapshot_shadow_label_is_immutable_and_ledger_neutral():
    from day_trading_engine.paper.replay import ReplayFidelity, ShadowOutcome
    from day_trading_engine.research.dataset import DecisionSnapshot, label_shadow

    snapshot = DecisionSnapshot(
        session="2026-08-25",
        symbol="S01",
        snapshot_at=datetime.now(UTC),
        cohort_bucket=CohortBucket.CORE,
        cohort_rank=1,
        final_shortlist=True,
        primary=False,
        eligible=True,
        score=0.5,
        algorithm_version="v1",
        config_version="v1",
        feature_version="v1",
        provider_version="v1",
        fidelity=ReplayFidelity.BAR_ONLY,
    )
    labeled = label_shadow(snapshot, ShadowOutcome(False, "no_trigger", 0.0, 0.0, None))
    assert labeled.snapshot == snapshot and labeled.ledger_affecting is False

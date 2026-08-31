from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from day_trading_engine.engine.universe import UniverseSelectionRow, UniverseSnapshot
from day_trading_engine.engine.universe_ledger import UniverseLedger
from day_trading_engine.paper.replay import ReplayBar
from day_trading_engine.research.cycle import (
    PromotionEvidence,
    ResearchRegistry,
    build_ablation_report,
    classify_regimes,
    generate_monthly_report,
    promotion_result,
)
from day_trading_engine.research.outcomes import evaluate_shadow_outcome

NOW = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)


def _member(symbol: str, security_id: str) -> UniverseSelectionRow:
    return UniverseSelectionRow(
        symbol=symbol,
        security_id=security_id,
        exchange="NASDAQ",
        asset_type="common_stock",
        sector="Technology",
        score=0.8,
        included=True,
        reason="selected by monthly universe score",
    )


def _snapshot(universe_id: str, effective: str, members) -> UniverseSnapshot:
    return UniverseSnapshot(
        universe_id=universe_id,
        effective_from=effective,
        selector_version="universe-v1",
        config_version="3.1",
        target=len(members),
        members=tuple(members),
        exclusions=(),
        created_at=f"{effective}T00:00:00+00:00",
        checksum="test",
    )


def test_regimes_are_deterministic_and_decision_time_only() -> None:
    row = {
        "eligible": True,
        "features": {
            "volatility_score": 0.8,
            "gap_pct": 0.03,
            "rvol": 2.5,
            "spread_pct": 0.002,
            "sector_score": 0.7,
        },
        "context": {
            "market_score": 0.8,
            "news_score": 0.7,
            "social_score": 0.6,
            "fundamental_score": 0.55,
            "evidence_counts": {"earnings": 1, "news": 3},
        },
        "future_outcome": 999,
    }
    first = classify_regimes(row)
    row["future_outcome"] = -999
    assert classify_regimes(row) == first
    assert first == {
        "version": "regime-v1",
        "market": "HIGH_VOLATILITY",
        "stock": "GAP_UP",
        "catalyst": "EARNINGS",
        "execution_data": "COMPLETE_TIGHT_SPREAD",
    }
    assert classify_regimes({"eligible": False})["execution_data"] == "HARD_GATE_REJECTED"


def test_universe_ledger_preserves_ticker_and_delisting_history(tmp_path) -> None:
    ledger = UniverseLedger(tmp_path / "universe.db")
    ledger.record_snapshot(_snapshot("u1", "2026-08-01", [_member("OLD", "sec-1")]))
    ledger.record_snapshot(_snapshot("u2", "2026-09-01", [_member("NEW", "sec-1")]))
    assert ledger.membership_as_of(date(2026, 9, 2)) == (("sec-1", "NEW"),)
    history = ledger.history("sec-1")
    assert history[0][:3] == ("OLD", "2026-08-01", "2026-09-01")
    assert history[1][:2] == ("NEW", "2026-09-01")
    ledger.record_delisting("sec-1", effective_on=date(2026, 9, 10), reason="delisted")
    assert ledger.membership_as_of(date(2026, 9, 10)) == ()
    with pytest.raises(KeyError):
        ledger.record_delisting("missing", effective_on=date(2026, 9, 10), reason="delisted")


def test_promotion_requires_all_gates_and_one_per_cycle(tmp_path) -> None:
    evidence = PromotionEvidence(
        experiment_id="exp-1",
        challenger_id="challenger",
        champion_id="champion",
        complete_sessions=20,
        triggered_setups=8,
        expectancy=0.02,
        champion_expectancy=0.01,
        max_drawdown=0.04,
        champion_drawdown=0.05,
        reproducible=True,
        forward_confirmed=True,
    )
    assert promotion_result(evidence) == "PROMOTED"
    assert promotion_result(
        PromotionEvidence(**{**evidence.__dict__, "forward_confirmed": False})
    ) == "NO CHANGE"

    registry = ResearchRegistry(tmp_path / "research.db")
    registry.register_dataset(
        "dataset-1",
        manifest_hash="abc",
        date_range="2026-08-01..2026-08-31",
        universe_versions=["u1", "u1"],
        schema_version="v3.1",
    )
    registry.register_algorithm(
        "champion",
        parent_id=None,
        git_commit="abc123",
        config_version="3.1",
        feature_version="feature-v1",
        weights={"technical": 0.5},
        status="CHAMPION",
    )
    registry.record_experiment(
        "exp-1",
        hypothesis="better ranking",
        champion="champion",
        challenger="challenger",
        train_period="2026-01..2026-05",
        validation_period="2026-06",
        holdout_period="2026-07",
    )
    registry.record_result(
        "exp-1",
        "dataset-1",
        metrics={"expectancy": 0.02},
        regime_metrics={"RANGE": 0.01},
        data_quality={"complete": True},
        result="PASS",
    )
    registry.consume_holdout("2026-07", "dataset-1", "exp-1")
    with pytest.raises(ValueError, match="already influenced"):
        registry.consume_holdout("2026-07", "dataset-1", "exp-2")
    assert registry.decide_cycle("2026-08", evidence) == "PROMOTED"
    assert registry.decide_cycle("2026-08", evidence) == "NO CHANGE"
    assert registry.summary()["counts"]["experiment_results"] == 1


def test_shadow_outcome_has_v31_path_timing_and_reference_labels() -> None:
    bars = [
        ReplayBar(NOW + timedelta(minutes=1), high=10.2, low=9.9, close=10.1),
        ReplayBar(NOW + timedelta(minutes=6), high=10.8, low=10.0, close=10.7),
        ReplayBar(NOW + timedelta(minutes=16), high=11.2, low=10.5, close=11.0),
    ]
    outcome = evaluate_shadow_outcome(
        {"symbol": "AAA", "entry": 10.0, "stop": 9.5, "target": 11.0, "quantity": 2},
        bars,
        snapshot_at=NOW,
    )
    assert outcome["entry_triggered"] is True
    assert outcome["target_before_stop"] is True
    assert outcome["time_to_entry_seconds"] == 60
    assert outcome["mfe_pct"] > 0
    assert outcome["shadow_return"] == pytest.approx(0.1)
    assert outcome["reference_returns"]["5m"] == pytest.approx(0.07)


def test_outcome_ablation_and_monthly_report_are_reproducible(tmp_path) -> None:
    month_dir = tmp_path / "data" / "research" / "2026" / "08"
    month_dir.mkdir(parents=True)
    candidates = []
    outcomes = []
    for index, score in enumerate((0.9, 0.7, 0.6)):
        symbol = f"S{index}"
        candidates.append(
            {
                "snapshot_id": "snap",
                "symbol": symbol,
                "session": "2026-08-28",
                "eligible": True,
                "plan": {"entry": 10.0},
                "technical_score": score,
                "universe_id": "u1",
                "algorithm_version": "orb-v1",
                "config_version": "3.1",
                "feature_version": "feature-v1",
                "context": {
                    "market_score": score,
                    "news_score": score,
                    "social_score": score,
                    "fundamental_score": score,
                },
            }
        )
        outcomes.append(
            {
                "snapshot_id": "snap",
                "symbol": symbol,
                "status": "complete",
                "entry_triggered": True,
                "target_before_stop": index == 0,
                "shadow_return": (0.02, -0.01, 0.01)[index],
                "mfe_pct": 0.03,
                "mae_pct": 0.01,
                "regimes": {
                    "market": "RANGE",
                    "stock": "MOMENTUM",
                    "catalyst": "COMPANY_NEWS",
                    "execution_data": "COMPLETE_TIGHT_SPREAD",
                },
            }
        )

    pd.DataFrame(
        [
            {"snapshot_id": row["snapshot_id"], "symbol": row["symbol"], "payload": json.dumps(row)}
            for row in candidates
        ]
    ).to_parquet(month_dir / "snap.candidates.parquet", index=False)
    pd.DataFrame(
        [
            {"snapshot_id": row["snapshot_id"], "symbol": row["symbol"], "payload": json.dumps(row)}
            for row in outcomes
        ]
    ).to_parquet(month_dir / "snap.outcomes.parquet", index=False)

    ablations = build_ablation_report(candidates, outcomes)
    assert len(ablations) == 5
    assert ablations[-1]["expectancy"] == pytest.approx(0.02)
    report_path = generate_monthly_report(tmp_path, "2026-08")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["data_quality"]["candidate_rows"] == 3
    assert report["regime_breakdown"]["market"] == {"RANGE": 3}
    assert report["promotion_policy"]["automatic_promotion"] is False
    assert (month_dir / "ablations.parquet").exists()
    assert generate_monthly_report(tmp_path, "2026-08") == report_path
    with pytest.raises(ValueError, match="YYYY-MM"):
        generate_monthly_report(tmp_path, "bad")

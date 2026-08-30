from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from day_trading_engine.engine.runner import (
    _available_cash,
    _benchmark_return,
    _market_score,
)
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.ops.scheduled import _record_shadow_outcomes
from day_trading_engine.paper.replay import ReplayBar
from day_trading_engine.research.outcomes import evaluate_shadow_outcome
from day_trading_engine.research.store import ResearchStore
from day_trading_engine.ui.state import ReportStore, SavedReport

NOW = datetime(2026, 8, 28, 16, 0, tzinfo=UTC)


def test_research_store_requires_and_preserves_exactly_thirty_rows(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research")
    rows = [
        {"symbol": f"S{i:02d}", "rank": i, "session": "2026-08-28"}
        for i in range(30)
    ]
    store.save_decision_rows("2026-08-28-snap", rows)
    store.save_decision_rows("2026-08-28-snap", rows)
    with pytest.raises(ValueError, match="exactly 30"):
        store.save_decision_rows("2026-08-28-short", rows[:29])
    changed = [dict(row) for row in rows]
    changed[0]["rank"] = 99
    with pytest.raises(ValueError, match="immutable"):
        store.save_decision_rows("2026-08-28-snap", changed)
    files = (tmp_path / "research" / "2026" / "08").glob("*.candidates.parquet")
    assert list(files)


def test_shadow_outcome_records_explicit_unavailable_reason() -> None:
    outcome = evaluate_shadow_outcome(
        None,
        (),
        snapshot_at=NOW,
        unavailable_reason="spread exceeds limit",
    )
    assert outcome == {
        "status": "unavailable",
        "reason": "spread exceeds limit",
        "fidelity": "BAR_ONLY",
    }


def test_shadow_outcome_preserves_shared_same_bar_ambiguity() -> None:
    outcome = evaluate_shadow_outcome(
        {"symbol": "AAA", "entry": 10.0, "stop": 9.0, "target": 11.0, "quantity": 1},
        [ReplayBar(NOW + timedelta(minutes=1), high=11.5, low=8.5, close=10.0)],
        snapshot_at=NOW,
    )
    assert outcome["outcome"] == "ambiguous_same_bar"
    assert outcome["fidelity"] == "BAR_ONLY"


def test_after_close_labels_all_30_without_changing_live_cash(tmp_path) -> None:
    session = "2026-08-28"
    cohort = [
        {
            "symbol": f"S{i:02d}",
            "reasons": [],
            "plan": {
                "symbol": f"S{i:02d}",
                "entry": 10.0,
                "stop": 9.0,
                "target": 11.0,
                "quantity": 1,
            },
        }
        for i in range(30)
    ]
    reports = ReportStore(tmp_path / "data" / "decision_state.db")
    reports.save_once(
        SavedReport(
            f"{session}-snapshot",
            NOW,
            "S00",
            {"session": session, "decision_state": "PRIMARY", "cohort": cohort},
        )
    )
    for row in cohort:
        target = (
            tmp_path
            / "data"
            / "historical"
            / "interval=OneMinute"
            / f"date={session}"
            / f"symbol={row['symbol']}"
            / "candles.parquet"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "start": NOW + timedelta(minutes=1),
                    "end": NOW + timedelta(minutes=2),
                    "open": 10.0,
                    "high": 11.5,
                    "low": 8.5,
                    "close": 10.0,
                    "volume": 1000,
                }
            ]
        ).to_parquet(target, index=False)

    assert _record_shadow_outcomes(tmp_path) == 30
    research = ResearchStore(tmp_path / "data" / "research.db")
    assert research.outcome_count(f"{session}-snapshot", session=session) == 30
    assert _available_cash(reports, 100.0) == 100.0


def test_realized_manual_pnl_compounds_available_cash(tmp_path) -> None:
    store = ReportStore(tmp_path / "state.db")
    store.save_once(
        SavedReport(
            "snap",
            NOW,
            "AAA",
            {
                "session": "2026-08-28",
                "primary": {
                    "symbol": "AAA",
                    "entry": 10.0,
                    "stop": 9.0,
                    "target": 12.0,
                    "quantity": 2,
                },
            },
        )
    )
    store.record_trade_entry(
        "snap", at=NOW + timedelta(minutes=1), price=10.0, quantity=2
    )
    store.record_trade_exit(
        "snap",
        at=NOW + timedelta(minutes=2),
        price=11.0,
        reason="manual close",
    )
    assert _available_cash(store, 100.0) == pytest.approx(102.0)


def test_market_normalization_and_missing_benchmark_fail_closed(tmp_path) -> None:
    assert _market_score(0.04, 0.0) == pytest.approx(1.0)
    assert _market_score(-0.04, 0.0) == pytest.approx(0.0)
    store = MarketDataStore(tmp_path / "trading.db")
    with pytest.raises(RuntimeError, match="critical market benchmark"):
        _benchmark_return(
            store,
            ("SPY", "QQQ"),
            session_date="2026-08-28",
            cutoff=NOW,
        )

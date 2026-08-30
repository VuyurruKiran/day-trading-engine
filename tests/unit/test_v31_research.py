from datetime import UTC, datetime, timedelta

import pytest

from day_trading_engine.engine.runner import _available_cash, _benchmark_return, _market_score
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.research.outcomes import evaluate_shadow_outcome
from day_trading_engine.research.store import ResearchStore
from day_trading_engine.ui.state import ReportStore, SavedReport

NOW = datetime(2026, 8, 28, 16, 0, tzinfo=UTC)


def test_research_store_requires_and_preserves_exactly_thirty_rows(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research")
    rows = [{"symbol": f"S{i:02d}", "rank": i, "session": "2026-08-28"} for i in range(30)]
    store.save_decision_rows("2026-08-28-snap", rows)
    store.save_decision_rows("2026-08-28-snap", rows)
    with pytest.raises(ValueError, match="exactly 30"):
        store.save_decision_rows("2026-08-28-short", rows[:29])
    changed = [dict(row) for row in rows]
    changed[0]["rank"] = 99
    with pytest.raises(ValueError, match="immutable"):
        store.save_decision_rows("2026-08-28-snap", changed)
    assert list((tmp_path / "research" / "2026" / "08").glob("*.candidates.parquet"))


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

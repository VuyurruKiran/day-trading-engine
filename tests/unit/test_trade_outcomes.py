from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from day_trading_engine.ui.state import ReportStore, SavedReport


def test_planned_vs_actual_outcome_uses_exact_snapshot(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    created = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
    report = store.save_once(
        SavedReport(
            snapshot_id="2026-08-27-exact",
            created_at=created,
            primary_symbol="AAPL",
            payload={
                "session": "2026-08-27",
                "decision_state": "PRIMARY",
                "primary": {
                    "symbol": "AAPL",
                    "entry": 100.0,
                    "stop": 98.0,
                    "target": 104.0,
                    "quantity": 2,
                },
            },
        )
    )

    store.record_trade_entry(
        report.snapshot_id,
        at=created + timedelta(minutes=1),
        price=100.25,
        quantity=2,
    )
    opened = store.trade_outcome(report.snapshot_id)

    assert opened.snapshot_id == report.snapshot_id
    assert opened.planned_entry == 100.0
    assert opened.planned_stop == 98.0
    assert opened.planned_target == 104.0
    assert opened.actual_entry == 100.25
    assert opened.realized_pnl is None

    store.record_trade_exit(
        report.snapshot_id,
        at=created + timedelta(minutes=30),
        price=103.25,
        reason="target",
    )
    closed = store.trade_outcome(report.snapshot_id)

    assert closed.actual_exit == 103.25
    assert closed.realized_pnl == 6.0
    assert closed.exit_reason == "target"


def test_invalid_primary_plan_does_not_persist_manual_entry(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    created = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
    report = store.save_once(
        SavedReport(
            snapshot_id="2026-08-27-incomplete",
            created_at=created,
            primary_symbol="AAPL",
            payload={
                "session": "2026-08-27",
                "decision_state": "PRIMARY",
                "primary": {"symbol": "AAPL", "entry": 100.0, "stop": 98.0},
            },
        )
    )

    with pytest.raises(ValueError, match="incomplete"):
        store.record_trade_entry(
            report.snapshot_id,
            at=created + timedelta(minutes=1),
            price=100.25,
            quantity=1,
        )

    with pytest.raises(KeyError):
        store.manual_trade(report.snapshot_id)
    with pytest.raises(KeyError):
        store.trade_outcome(report.snapshot_id)

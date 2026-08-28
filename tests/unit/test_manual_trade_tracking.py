from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from day_trading_engine.ui.state import ReportStore, SavedReport


def _saved_report(store: ReportStore, *, primary: str | None = "AAPL") -> SavedReport:
    """Persist one deterministic report for manual-trade tests."""
    report = SavedReport(
        snapshot_id="2026-08-27-test",
        created_at=datetime(2026, 8, 27, 14, 0, tzinfo=UTC),
        primary_symbol=primary,
        payload={"session": "2026-08-27", "decision_state": "PRIMARY"},
    )
    return store.save_once(report)


def test_manual_trade_entry_and_exit_are_linked_to_snapshot(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    report = _saved_report(store)
    entry_at = report.created_at + timedelta(minutes=1)
    exit_at = entry_at + timedelta(minutes=30)

    opened = store.record_trade_entry(
        report.snapshot_id,
        at=entry_at,
        price=101.25,
        quantity=2,
        notes="manual fill",
    )
    assert store.has_open_execution() is True
    assert opened.symbol == "AAPL"
    assert opened.quantity == 2

    closed = store.record_trade_exit(
        report.snapshot_id,
        at=exit_at,
        price=103.0,
        reason="target",
        notes="filled cleanly",
    )

    assert closed.exit_reason == "target"
    assert closed.exit_price == 103.0
    assert "manual fill" in closed.notes
    assert "filled cleanly" in closed.notes
    assert store.has_open_execution() is False


def test_manual_entry_requires_primary_snapshot(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    report = _saved_report(store, primary=None)

    with pytest.raises(ValueError, match="PRIMARY"):
        store.record_trade_entry(
            report.snapshot_id,
            at=report.created_at + timedelta(minutes=1),
            price=10.0,
            quantity=1,
        )


def test_manual_exit_rejects_time_before_entry(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    report = _saved_report(store)
    entry_at = report.created_at + timedelta(minutes=5)
    store.record_trade_entry(
        report.snapshot_id,
        at=entry_at,
        price=10.0,
        quantity=1,
    )

    with pytest.raises(ValueError, match="precede"):
        store.record_trade_exit(
            report.snapshot_id,
            at=entry_at - timedelta(minutes=1),
            price=10.1,
            reason="manual",
        )

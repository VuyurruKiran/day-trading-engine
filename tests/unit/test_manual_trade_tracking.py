from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from day_trading_engine.ui.state import ReportStore, SavedReport


def _saved_report(
    store: ReportStore,
    *,
    primary: str | None = "AAPL",
    session: str = "2026-08-27",
    snapshot_id: str = "2026-08-27-test",
    created_at: datetime | None = None,
) -> SavedReport:
    """Persist one deterministic report for manual-trade tests."""
    payload: dict[str, object] = {
        "session": session,
        "decision_state": "PRIMARY" if primary else "NO_TRADE",
    }
    if primary:
        payload["primary"] = {
            "symbol": primary,
            "entry": 101.0,
            "stop": 99.0,
            "target": 105.0,
            "quantity": 1,
        }
    report = SavedReport(
        snapshot_id=snapshot_id,
        created_at=created_at or datetime(2026, 8, 27, 14, 0, tzinfo=UTC),
        primary_symbol=primary,
        payload=payload,
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


def test_manual_entry_rejects_time_before_decision(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    report = _saved_report(store)

    with pytest.raises(ValueError, match="precede decision"):
        store.record_trade_entry(
            report.snapshot_id,
            at=report.created_at - timedelta(seconds=1),
            price=10.0,
            quantity=1,
        )


def test_manual_entry_rejects_second_active_position(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "decision_state.db")
    first = _saved_report(store)
    second_created = first.created_at + timedelta(days=1)
    second = _saved_report(
        store,
        primary="MSFT",
        session="2026-08-28",
        snapshot_id="2026-08-28-test",
        created_at=second_created,
    )
    store.record_trade_entry(
        first.snapshot_id,
        at=first.created_at + timedelta(minutes=1),
        price=10.0,
        quantity=1,
    )

    with pytest.raises(ValueError, match="active position"):
        store.record_trade_entry(
            second.snapshot_id,
            at=second.created_at + timedelta(minutes=1),
            price=20.0,
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

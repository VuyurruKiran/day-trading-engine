from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from day_trading_engine.ui.state import ReportStore, SavedReport


def test_save_once_serializes_same_session_writers(tmp_path) -> None:
    """Concurrent writers must converge on one persisted report per session."""
    store = ReportStore(tmp_path / "decision_state.db")
    reports = tuple(
        SavedReport(
            snapshot_id=f"snapshot-{index}",
            created_at=datetime(2026, 8, 26, 14, index, tzinfo=UTC),
            primary_symbol="AAPL",
            payload={"session": "2026-08-26", "decision": "PRIMARY"},
        )
        for index in range(2)
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = tuple(pool.map(store.save_once, reports))

    assert saved[0].snapshot_id == saved[1].snapshot_id
    assert store.latest() is not None
    assert store.latest().snapshot_id == saved[0].snapshot_id

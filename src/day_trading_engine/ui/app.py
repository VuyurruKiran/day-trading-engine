"""Compatibility helpers for callers of the retired Streamlit UI module."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from day_trading_engine.ui.server import _read_backup_status
from day_trading_engine.ui.state import ManualTrade


def _local_datetime(day: date, clock: time, timezone: str) -> datetime:
    """Combine UI date/time input into one aware local timestamp."""
    return datetime.combine(day, clock, tzinfo=ZoneInfo(timezone))


def _manual_form_snapshot(
    latest_snapshot_id: str,
    latest_has_primary: bool,
    manual_history: tuple[ManualTrade, ...],
) -> str | None:
    """Prefer an open trade so its exit remains available across later decisions."""
    open_trade = next((trade for trade in manual_history if trade.exit_at is None), None)
    if open_trade is not None:
        return open_trade.snapshot_id
    return latest_snapshot_id if latest_has_primary else None


__all__ = ["_local_datetime", "_manual_form_snapshot", "_read_backup_status"]

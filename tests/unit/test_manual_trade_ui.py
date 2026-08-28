from datetime import date, time
from zoneinfo import ZoneInfo

from day_trading_engine.ui.app import _local_datetime, _manual_form_snapshot
from day_trading_engine.ui.state import ManualTrade


def test_manual_trade_ui_combines_local_date_and_time() -> None:
    value = _local_datetime(date(2026, 8, 27), time(10, 15), "America/Edmonton")

    assert value.tzinfo == ZoneInfo("America/Edmonton")
    assert value.isoformat() == "2026-08-27T10:15:00-06:00"


def test_manual_trade_ui_keeps_open_trade_exit_accessible_after_newer_no_trade() -> None:
    open_trade = ManualTrade(
        snapshot_id="older-primary",
        symbol="AAPL",
        entry_at="2026-08-27T16:01:00+00:00",
        entry_price=100.0,
        quantity=1,
        exit_at=None,
        exit_price=None,
        exit_reason=None,
        notes="",
    )

    assert _manual_form_snapshot("newer-no-trade", False, (open_trade,)) == "older-primary"
    assert _manual_form_snapshot("new-primary", True, ()) == "new-primary"
    assert _manual_form_snapshot("newer-no-trade", False, ()) is None

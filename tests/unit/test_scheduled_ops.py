from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.ops.scheduled import _extended_session_complete, latest_completed_session

ROOT = Path(__file__).resolve().parents[2]


def test_latest_completed_session_skips_weekend() -> None:
    assert latest_completed_session(date(2026, 8, 31)) == date(2026, 8, 28)


def test_extended_session_is_not_complete_at_regular_close() -> None:
    eastern = ZoneInfo("America/New_York")
    session = date(2026, 9, 1)
    assert not _extended_session_complete(session, datetime(2026, 9, 1, 16, tzinfo=eastern))
    assert _extended_session_complete(session, datetime(2026, 9, 1, 20, tzinfo=eastern))


def test_local_schedulers_cover_required_jobs() -> None:
    powershell = (ROOT / "schedule-local.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "schedule-local.sh").read_text(encoding="utf-8")
    required = ("quality", "history", "after-close", "backup", "snapshot")

    for script in (powershell, shell):
        for token in required:
            assert token in script

    assert "Get-Command uv" in powershell
    assert 'DayTradingEngine-ScanDecision" "06:00"' in powershell
    assert "run.ps1" in powershell
    assert "-WakeToRun" in powershell
    assert "-StartWhenAvailable" in powershell
    assert "-RestartCount 3" in powershell
    assert 'DayTradingEngine-AfterClose" "18:05"' in powershell
    assert 'DayTradingEngine-Backup" "18:20"' in powershell
    assert 'DayTradingEngine-MonthEndSnapshot" "18:30"' in powershell
    assert "command -v uv" in shell
    assert "engine.live" in shell
    assert "cron paths containing % are unsupported" in shell
    assert "5 18 * * *" in shell
    assert "20 18 * * *" in shell
    assert "30 18 * * *" in shell
    assert "snapshotDestination" not in powershell
    assert "snapshot_destination" not in shell

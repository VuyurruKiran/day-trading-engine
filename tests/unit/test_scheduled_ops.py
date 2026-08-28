from datetime import date
from pathlib import Path

from day_trading_engine.ops.scheduled import latest_completed_session

ROOT = Path(__file__).resolve().parents[2]


def test_latest_completed_session_skips_weekend() -> None:
    assert latest_completed_session(date(2026, 8, 31)) == date(2026, 8, 28)


def test_local_schedulers_cover_required_jobs() -> None:
    powershell = (ROOT / "schedule-local.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "schedule-local.sh").read_text(encoding="utf-8")
    required = ("quality", "history", "engine.live", "after-close", "backup", "snapshot")

    for script in (powershell, shell):
        for token in required:
            assert token in script

    assert "Get-Command uv" in powershell
    assert 'DayTradingEngine-AfterClose" "14:30"' in powershell
    assert 'DayTradingEngine-Backup" "14:45"' in powershell
    assert 'DayTradingEngine-MonthEndSnapshot" "15:00"' in powershell
    assert "command -v uv" in shell
    assert "cron paths containing % are unsupported" in shell
    assert "30 14 * * *" in shell
    assert "45 14 * * *" in shell
    assert "0 15 * * *" in shell
    assert "snapshotDestination" not in powershell
    assert "snapshot_destination" not in shell

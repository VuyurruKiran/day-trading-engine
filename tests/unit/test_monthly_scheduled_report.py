from datetime import date
from pathlib import Path

import day_trading_engine.ops.scheduled as scheduled


def test_monthly_report_uses_last_completed_trading_session(
    tmp_path: Path, monkeypatch
) -> None:
    class MonthEndDateTime:
        @classmethod
        def now(cls, timezone):
            from datetime import datetime, time

            return datetime.combine(date(2026, 8, 31), time(20), timezone)

    monkeypatch.setattr(scheduled, "datetime", MonthEndDateTime)
    def sessions(start, end):
        return [date(2026, 8, 31)] if start == end else [date(2026, 9, 1)]

    monkeypatch.setattr(scheduled, "_sessions", sessions)
    captured = []
    monkeypatch.setattr(
        scheduled,
        "generate_monthly_report",
        lambda root, month: captured.append((root, month)) or tmp_path / "monthly_report.json",
    )

    assert scheduled._monthly_report(tmp_path) == 0
    assert captured == [(tmp_path, "2026-08")]

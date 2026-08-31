from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.ops.scheduled as scheduled
from day_trading_engine.ui.state import ReportStore, SavedReport

SESSION = "2026-08-28"


def _report() -> SavedReport:
    cohort = [
        {
            "symbol": f"T{index:02d}",
            "plan": None,
            "reasons": ["not eligible"],
            "features": {},
            "eligible": False,
        }
        for index in range(30)
    ]
    return SavedReport(
        "snap-1",
        datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
        None,
        {
            "session": SESSION,
            "decision_state": "NO_TRADE",
            "cohort": cohort,
        },
    )


def test_incomplete_reports_reads_persisted_full_cohort(tmp_path: Path) -> None:
    state = tmp_path / "data" / "decision_state.db"
    ReportStore(state).save_once(_report())

    pending = scheduled._incomplete_reports(tmp_path)

    assert tuple(item.snapshot_id for item in pending) == ("snap-1",)
    assert scheduled._incomplete_reports(tmp_path / "missing") == ()


def test_record_report_outcomes_processes_every_cohort_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[tuple[str, str, dict[str, object]]] = []

    class Research:
        def __init__(self, path: Path) -> None:
            self.path = path

        def outcome_count(self, snapshot_id: str, *, session: str) -> int:
            return len(recorded)

        def record_outcome(
            self,
            snapshot_id: str,
            symbol: str,
            outcome: dict[str, object],
            *,
            recorded_at: datetime,
            session: str,
        ) -> None:
            assert recorded_at.tzinfo is not None
            recorded.append((snapshot_id, symbol, outcome))

    monkeypatch.setattr(scheduled, "ResearchStore", Research)
    monkeypatch.setattr(scheduled, "load_replay_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        scheduled,
        "evaluate_shadow_outcome",
        lambda plan, bars, **kwargs: {"status": "unavailable", "reason": kwargs["unavailable_reason"]},
    )
    monkeypatch.setattr(
        scheduled,
        "classify_regimes",
        lambda row: {"market": "RANGE"},
    )

    count = scheduled._record_report_outcomes(tmp_path, _report())

    assert count == 30
    assert len(recorded) == 30
    assert recorded[0][1] == "T00"
    assert recorded[0][2]["reason"] == "not eligible"
    assert recorded[0][2]["regimes"] == {"market": "RANGE"}


def test_record_shadow_outcomes_uses_latest_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report()

    class Reports:
        def __init__(self, path: Path) -> None:
            self.path = path

        def latest(self):
            return report

    monkeypatch.setattr(scheduled, "ReportStore", Reports)
    monkeypatch.setattr(scheduled, "_record_report_outcomes", lambda root, saved: 30)
    assert scheduled._record_shadow_outcomes(tmp_path) == 30

    monkeypatch.setattr(Reports, "latest", lambda self: None)
    assert scheduled._record_shadow_outcomes(tmp_path) == 0

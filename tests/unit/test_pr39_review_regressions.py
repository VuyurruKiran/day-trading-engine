from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.ops.scheduled as scheduled


def test_after_close_cleanup_survives_shadow_outcome_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    report = SimpleNamespace(payload={"session": "2026-08-28"})

    def fail_outcomes(_: Path, __: SimpleNamespace) -> int:
        raise ValueError("bad persisted research row")

    class Store:
        def __init__(self, path: Path) -> None:
            self.path = path

        def delete_before(self, cutoff: datetime) -> int:
            calls.append("delete")
            return 1

        def vacuum(self) -> None:
            calls.append("vacuum")

    monkeypatch.setattr(scheduled, "_incomplete_reports", lambda _: (report,))
    monkeypatch.setattr(scheduled, "_history_session", lambda *_: 0)
    monkeypatch.setattr(scheduled, "_record_report_outcomes", fail_outcomes)
    monkeypatch.setattr(scheduled, "MarketDataStore", Store)

    assert scheduled._after_close(tmp_path, 30) == 2
    assert calls == ["delete", "vacuum"]

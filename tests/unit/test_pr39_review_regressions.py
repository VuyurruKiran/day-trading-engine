from datetime import datetime
from pathlib import Path

import pytest

import day_trading_engine.ops.scheduled as scheduled


def test_after_close_cleanup_survives_shadow_outcome_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fail_outcomes(_: Path) -> int:
        raise ValueError("bad persisted research row")

    class Store:
        def __init__(self, path: Path) -> None:
            self.path = path

        def delete_before(self, cutoff: datetime) -> int:
            calls.append("delete")
            return 1

        def vacuum(self) -> None:
            calls.append("vacuum")

    monkeypatch.setattr(scheduled, "_record_shadow_outcomes", fail_outcomes)
    monkeypatch.setattr(scheduled, "MarketDataStore", Store)

    assert scheduled._after_close(tmp_path, 30) == 2
    assert calls == ["delete", "vacuum"]

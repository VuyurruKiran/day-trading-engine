from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from day_trading_engine.core.config import load_config
from day_trading_engine.engine.cohort import CohortMember, CohortResult
from day_trading_engine.engine.discovery import BroadScanScore
from day_trading_engine.engine.live import run_live

ROOT = Path(__file__).resolve().parents[2]


def test_live_freezes_first_complete_cohort_for_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    symbols = tuple(f"T{index:02d}" for index in range(30))
    cohort = CohortResult(
        tuple(
            CohortMember(symbol, index + 1, "core", "selected")
            for index, symbol in enumerate(symbols)
        ),
        0,
    )
    scores = tuple(
        BroadScanScore(symbol, 1.0, {}, True, "selected") for symbol in symbols
    )
    refreshes: list[tuple[str, ...]] = []
    selection_calls = 0
    waits = 0

    class Collector:
        store = object()

        def prepare(self, symbols):
            return ()

        def collect(self, symbols):
            stored = tuple(SimpleNamespace(symbol=s) for s in symbols)
            return SimpleNamespace(stored=stored, failed_symbols=())

    class Reports:
        def latest(self):
            return None

    def select(*_args, **_kwargs):
        nonlocal selection_calls
        selection_calls += 1
        return cohort, scores

    def refresh(root, selected, *, software_version):
        refreshes.append(tuple(selected))
        return 0, datetime.now(UTC)

    def wait(deadline, poll_seconds):
        nonlocal waits
        waits += 1
        if waits == 4:
            raise KeyboardInterrupt
        return deadline

    monkeypatch.setattr("day_trading_engine.engine.live.load_config", lambda _: config)
    monkeypatch.setattr(
        "day_trading_engine.engine.live.load_scan_universe",
        lambda *_args, **_kwargs: symbols,
    )
    monkeypatch.setattr(
        "day_trading_engine.engine.live.load_universe_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(symbols=symbols),
    )
    monkeypatch.setattr(
        "day_trading_engine.engine.live.build_default_collector",
        lambda *_: Collector(),
    )
    monkeypatch.setattr("day_trading_engine.engine.live.ReportStore", lambda *_: Reports())
    monkeypatch.setattr(
        "day_trading_engine.engine.live._regular_session_timestamp",
        lambda _: True,
    )
    monkeypatch.setattr(
        "day_trading_engine.engine.live._decision_time_reached",
        lambda *_: True,
    )
    monkeypatch.setattr("day_trading_engine.engine.live.select_research_cohort", select)
    monkeypatch.setattr("day_trading_engine.engine.live._refresh_context", refresh)
    monkeypatch.setattr(
        "day_trading_engine.engine.live.run_decision",
        lambda **_: SimpleNamespace(payload={"decision_state": "DATA_NOT_READY"}),
    )
    monkeypatch.setattr("day_trading_engine.engine.live._wait_for_next_poll", wait)

    with pytest.raises(KeyboardInterrupt):
        run_live(tmp_path, poll_seconds=1)

    assert selection_calls == 1
    assert refreshes == [symbols]

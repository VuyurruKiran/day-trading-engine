from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from day_trading_engine.core.config import load_config
from day_trading_engine.engine.live import run_live

ROOT = Path(__file__).resolve().parents[2]


def test_live_context_cache_uses_stable_cohort_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    cohort_a = tuple(f"T{index:02d}" for index in range(30))
    cohort_b = (*cohort_a[:-1], "T30")
    selections = iter((cohort_a, tuple(reversed(cohort_a)), cohort_b, cohort_a))
    refreshes: list[tuple[str, ...]] = []
    waits = 0

    class Collector:
        store = object()

        def prepare(self, symbols):
            return ()

        def collect(self, symbols):
            return SimpleNamespace(stored=cohort_a, failed_symbols=())

    class Reports:
        def latest(self):
            return None

    def refresh(root, symbols, *, software_version):
        refreshes.append(tuple(symbols))
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
        lambda *_: cohort_a,
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
    monkeypatch.setattr(
        "day_trading_engine.engine.live.select_research_symbols",
        lambda *_args, **_kwargs: next(selections),
    )
    monkeypatch.setattr("day_trading_engine.engine.live._refresh_context", refresh)
    monkeypatch.setattr(
        "day_trading_engine.engine.live.run_decision",
        lambda **_: SimpleNamespace(payload={"decision_state": "DATA_NOT_READY"}),
    )
    monkeypatch.setattr("day_trading_engine.engine.live._wait_for_next_poll", wait)

    with pytest.raises(KeyboardInterrupt):
        run_live(tmp_path, poll_seconds=1)

    assert refreshes == [cohort_a, cohort_b]

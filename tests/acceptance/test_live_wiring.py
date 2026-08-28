from pathlib import Path

from day_trading_engine.core.config import load_config
from day_trading_engine.engine import live

ROOT = Path(__file__).resolve().parents[2]


def test_run_wires_live_engine_to_custom_ui() -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    assert len(config.market_data.watchlist) == config.research.daily_candidate_count == 30
    assert config.runtime.ui == "custom-local"

    powershell = (ROOT / "run.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "run.sh").read_text(encoding="utf-8")
    for script in (powershell, shell):
        assert "day_trading_engine.engine.live" in script
        assert "day_trading_engine.ui.server" in script
        assert "streamlit" not in script


def test_poll_wait_uses_remaining_interval(monkeypatch) -> None:
    slept: list[float] = []
    monotonic = iter((105.0,))
    monkeypatch.setattr(live.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(live.time, "sleep", slept.append)

    assert live._wait_for_next_poll(100.0, 60) == 160.0
    assert slept == [55.0]

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from day_trading_engine.engine.runner import _has_opening_coverage
from day_trading_engine.ops import scheduled
from day_trading_engine.providers import alpaca_history


@pytest.mark.parametrize("resource", ["bars", "trades"])
@pytest.mark.parametrize("payload", [{}, {"next_page_token": None}, [], {"wrong": []}])
def test_missing_alpaca_collection_cannot_prove_empty_history(
    tmp_path, monkeypatch, resource, payload
):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.setattr(
        alpaca_history, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode())
    )
    client = alpaca_history.AlpacaHistoryClient(["AAPL"], root=tmp_path)
    with pytest.raises(alpaca_history.AlpacaHistoryError, match="malformed"):
        if resource == "trades":
            client.missing_minutes_have_no_bar_eligible_trades(
                "AAPL", ("2026-04-01T13:31:00+00:00",)
            )
        else:
            client.get_candles(
                "AAPL",
                start=datetime(2026, 4, 1, 13, 30, tzinfo=UTC),
                end=datetime(2026, 4, 1, 20, tzinfo=UTC),
            )


@pytest.mark.parametrize("failure", [None, "close", "report", "backup", "snapshot"])
@pytest.mark.parametrize("raises", [False, True])
def test_after_close_chain_stops_at_first_failure(tmp_path, monkeypatch, failure, raises):
    called = []
    steps = ["close", "report", "backup", "snapshot"]

    def operation(name):
        def run(root, *args):
            assert root == tmp_path
            if name in {"backup", "snapshot"}:
                assert args == (tmp_path / "backups",)
            called.append(name)
            if name == failure:
                if raises:
                    raise OSError("test failure")
                return 2
            return 0

        return run

    monkeypatch.setattr(scheduled, "project_root", lambda: tmp_path)
    for name, function in zip(
        steps, ["_after_close", "_monthly_report", "_backup", "_snapshot"], strict=True
    ):
        monkeypatch.setattr(scheduled, function, operation(name))
    status = scheduled.main(["after-close", "--destination", str(tmp_path / "backups")])
    assert status == (2 if failure else 0)
    assert called == (steps[: steps.index(failure) + 1] if failure else steps)


def test_after_close_without_destination_remains_standalone(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduled, "project_root", lambda: tmp_path)
    monkeypatch.setattr(scheduled, "_after_close", lambda *a: 0)
    monkeypatch.setattr(scheduled, "_monthly_report", lambda *a: pytest.fail("unexpected report"))
    assert scheduled.main(["after-close"]) == 0


def test_opening_coverage_uses_market_minutes_despite_delayed_receipt():
    source = pd.date_range("2026-08-28T13:30:00Z", periods=6, freq="min")
    frame = pd.DataFrame({"source_at": source, "received_at": source + pd.Timedelta(minutes=1)})
    assert _has_opening_coverage(frame)
    assert not _has_opening_coverage(frame.drop(columns="source_at"))
    frame.loc[0, "source_at"] = pd.NaT
    assert not _has_opening_coverage(frame)


def test_manual_shell_launcher_forwards_optional_engine_arguments():
    root = Path(__file__).resolve().parents[2]
    script = (root / "run.sh").read_text(encoding="utf-8")
    assert 'day_trading_engine.engine.live "$@"' in script
    assert "--stop-after-extended-close" not in script

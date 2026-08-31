from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import day_trading_engine.ops.research as research_ops
from day_trading_engine.engine.universe import UniverseSelectionRow, UniverseSnapshot
from day_trading_engine.ops.data_protection import create_backup


def _snapshot() -> UniverseSnapshot:
    row = UniverseSelectionRow(
        symbol="AAA",
        security_id="sec-1",
        exchange="NYSE",
        asset_type="common_stock",
        sector="Industrials",
        score=0.8,
        included=True,
        reason="selected",
    )
    return UniverseSnapshot(
        universe_id="u1",
        effective_from="2026-08-01",
        selector_version="universe-v1",
        config_version="3.1",
        target=1,
        members=(row,),
        exclusions=(),
        created_at="2026-08-01T00:00:00+00:00",
        checksum="test",
    )


def test_month_subtraction_handles_month_end() -> None:
    assert research_ops._months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    with pytest.raises(ValueError, match="positive"):
        research_ops._months_before(date(2026, 3, 31), 0)


def test_sync_universe_records_membership(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(research_ops, "load_universe_snapshot", lambda *_args, **_kwargs: _snapshot())
    assert research_ops.sync_universe(tmp_path, date(2026, 8, 1)) == 1
    with sqlite3.connect(tmp_path / "data" / "universe.db") as db:
        assert db.execute("SELECT current_symbol FROM securities").fetchone() == ("AAA",)


def test_backfill_active_universe_uses_snapshot_and_benchmarks(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"coverage": {"current_request_complete": True}}))
    calls = {}

    monkeypatch.setattr(research_ops, "load_universe_snapshot", lambda *_args, **_kwargs: _snapshot())
    monkeypatch.setattr(
        research_ops,
        "load_config",
        lambda *_: SimpleNamespace(
            research_universe=SimpleNamespace(benchmark_symbols=("SPY", "QQQ"))
        ),
    )

    class Client:
        provider = "alpaca"

        def __init__(self, *, symbols, root) -> None:
            calls["client_symbols"] = symbols

    monkeypatch.setattr(research_ops, "AlpacaHistoryClient", Client)
    monkeypatch.setattr(research_ops, "write_universe_manifest", lambda *args, **kwargs: None)

    def backfill(client, *, symbols, start, end, root, workers):
        calls.update(symbols=symbols, start=start, end=end, workers=workers)
        return manifest

    monkeypatch.setattr(research_ops, "backfill_one_minute_history_concurrent", backfill)
    assert research_ops.backfill_active_universe(tmp_path, date(2026, 8, 31), 24) == 0
    assert calls["symbols"] == ["AAA", "SPY", "QQQ"]
    assert calls["start"] == date(2024, 8, 31)


def test_restore_drill_checks_sqlite_and_parquet(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "state.db") as db:
        db.execute("CREATE TABLE t (value INTEGER)")
        db.execute("INSERT INTO t VALUES (1)")
    pd.DataFrame([{"value": 1}]).to_parquet(data / "sample.parquet", index=False)
    backup = create_backup(data, tmp_path / "backup")
    assert research_ops.restore_drill(backup) == 0


def test_research_cli_reports_failures(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(research_ops, "project_root", lambda: tmp_path)
    monkeypatch.setattr(
        research_ops,
        "generate_monthly_report",
        lambda *_: (_ for _ in ()).throw(ValueError("bad month")),
    )
    assert research_ops.main(["--root", str(tmp_path), "monthly-report", "--month", "bad"]) == 2
    assert "monthly-report failed: bad month" in capsys.readouterr().out

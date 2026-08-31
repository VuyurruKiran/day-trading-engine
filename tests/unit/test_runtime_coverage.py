from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.engine.live as live
import day_trading_engine.market_data.collector as collector_module
import day_trading_engine.ops.maintenance as maintenance
import day_trading_engine.ops.scheduled as scheduled
from day_trading_engine.core.config import load_config
from day_trading_engine.engine.cohort import CohortMember, CohortResult
from day_trading_engine.engine.discovery import BroadScanScore
from day_trading_engine.market_data.collector import CollectionResult, QuestradeCollector
from day_trading_engine.providers.questrade import Market, QuestradeError, SymbolMatch

ROOT = Path(__file__).resolve().parents[2]


def _scheduled_config() -> SimpleNamespace:
    return SimpleNamespace(
        strategy=SimpleNamespace(family="orb"),
        project=SimpleNamespace(plan_version="3.1"),
        research_universe=SimpleNamespace(benchmark_symbols=("SPY", "QQQ")),
    )


def test_scheduled_history_and_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _scheduled_config()
    monkeypatch.setattr(scheduled, "load_config", lambda _: config)
    monkeypatch.setattr(
        scheduled,
        "load_scan_universe",
        lambda *_args, **_kwargs: ("AAPL", "MSFT"),
    )
    monkeypatch.setattr(
        scheduled, "latest_completed_session", lambda _: date(2026, 8, 28)
    )
    monkeypatch.setattr(
        scheduled,
        "AlpacaHistoryClient",
        lambda symbols, root: SimpleNamespace(provider="alpaca"),
    )
    monkeypatch.setattr(
        scheduled, "write_universe_manifest", lambda *a, **k: tmp_path / "u.json"
    )
    manifest = tmp_path / "coverage.json"
    manifest.write_text(
        json.dumps({"coverage": {"current_request_complete": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        scheduled,
        "backfill_one_minute_history_concurrent",
        lambda *a, **k: manifest,
    )
    assert scheduled._history(tmp_path) == 0

    class Store:
        def __init__(self, path: Path) -> None:
            self.path = path

        def delete_before(self, cutoff: datetime) -> int:
            assert cutoff.tzinfo is not None
            return 2

        def vacuum(self) -> None:
            return None

    monkeypatch.setattr(scheduled, "MarketDataStore", Store)
    assert scheduled._after_close(tmp_path, 30) == 0

    report = SimpleNamespace(ok=True, to_dict=lambda: {"ok": True})
    monkeypatch.setattr(scheduled, "run_health_check", lambda _: (report, config))
    assert scheduled._quality(tmp_path) == 0

    monkeypatch.setattr(scheduled, "create_backup", lambda *a: tmp_path / "backup")
    assert scheduled._backup(tmp_path, tmp_path / "dest") == 0


def test_scheduled_snapshot_and_main_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_datetime = datetime

    class MonthEndDateTime:
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 8, 31, 12, tzinfo=tz)

    config = _scheduled_config()
    monkeypatch.setattr(scheduled, "datetime", MonthEndDateTime)
    monkeypatch.setattr(scheduled, "load_config", lambda _: config)
    monkeypatch.setattr(
        scheduled, "create_month_end_snapshot", lambda *a, **k: tmp_path / "snap"
    )
    assert scheduled._snapshot(tmp_path, tmp_path / "dest") == 0

    monkeypatch.setattr(scheduled, "project_root", lambda: tmp_path)
    monkeypatch.setattr(scheduled, "_history", lambda _: 11)
    monkeypatch.setattr(scheduled, "_after_close", lambda *_: 12)
    monkeypatch.setattr(scheduled, "_quality", lambda _: 13)
    monkeypatch.setattr(scheduled, "_backup", lambda *_: 14)
    monkeypatch.setattr(scheduled, "_snapshot", lambda *_: 15)
    assert scheduled.main(["history"]) == 11
    assert scheduled.main(["after-close"]) == 12
    assert scheduled.main(["quality"]) == 13
    assert scheduled.main(["backup", str(tmp_path / "b")]) == 14
    assert scheduled.main(["snapshot", str(tmp_path / "s")]) == 15
    monkeypatch.setattr(
        scheduled,
        "_history",
        lambda _: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert scheduled.main(["history"]) == 2


def test_maintenance_validation_and_command_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert maintenance._symbols([" aapl ", "msft"]) == ["AAPL", "MSFT"]
    with pytest.raises(ValueError):
        maintenance._symbols(["AAPL", "aapl"])
    with pytest.raises(ValueError):
        maintenance._symbols(["AAPL=x"])

    assert maintenance._backfill_status({}) == 2
    assert (
        maintenance._backfill_status(
            {"entries": [], "coverage": {}, "current_request_keys": [1]}
        )
        == 2
    )
    failed = {
        "entries": [{"key": "k", "status": "failed"}],
        "coverage": {"current_request_complete": True},
        "current_request_keys": ["k"],
    }
    assert maintenance._backfill_status(failed) == 2
    assert maintenance._backfill_status(
        {"entries": [], "coverage": {}, "current_request_keys": []}
    ) == 3
    complete = {
        "entries": [],
        "coverage": {"current_request_complete": True},
        "current_request_keys": [],
    }
    assert maintenance._backfill_status(complete) == 0

    backup_target = tmp_path / "made-backup"
    backup_target.mkdir()
    (backup_target / "manifest.json").write_text(
        json.dumps({"same_volume_as_source": True}), encoding="utf-8"
    )
    monkeypatch.setattr(maintenance, "create_backup", lambda *a: backup_target)
    assert maintenance.main(
        ["--root", str(tmp_path), "backup", str(tmp_path / "dest")]
    ) == 0

    monkeypatch.setattr(maintenance, "restore_backup", lambda *a, **k: None)
    assert maintenance.main(
        [
            "--root",
            str(tmp_path),
            "restore",
            str(backup_target),
            str(tmp_path / "restore"),
            "--verify-only",
        ]
    ) == 0

    monkeypatch.setattr(
        maintenance, "create_month_end_snapshot", lambda *a, **k: tmp_path / "snap"
    )
    assert maintenance.main(
        [
            "--root",
            str(tmp_path),
            "snapshot",
            str(tmp_path / "dest"),
            "--month",
            "2026-08",
            "--algorithm",
            "a",
            "--config-version",
            "3.1",
            "--schema",
            "1",
        ]
    ) == 0


def test_maintenance_cleanup_bootstrap_and_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Store:
        def __init__(self, path: Path) -> None:
            self.path = path

        def delete_before(self, cutoff: datetime) -> int:
            return 1

        def vacuum(self) -> None:
            return None

    monkeypatch.setattr(maintenance, "MarketDataStore", Store)
    assert maintenance.main(
        ["--root", str(tmp_path), "cleanup-trading-db", "--days", "7"]
    ) == 0

    universe = tmp_path / "universe.json"
    monkeypatch.setattr(maintenance, "write_universe_manifest", lambda *a, **k: universe)
    assert maintenance.main(
        ["--root", str(tmp_path), "bootstrap-universe", "--as-of", "2026-08-28", "AAPL"]
    ) == 0

    class Client:
        provider = "alpaca"

        def __init__(self, symbols, root):
            self.symbols = symbols

    manifest = tmp_path / "coverage.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [],
                "coverage": {"current_request_complete": True},
                "current_request_keys": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(maintenance, "AlpacaHistoryClient", Client)
    monkeypatch.setattr(
        maintenance,
        "backfill_one_minute_history_concurrent",
        lambda *a, **k: manifest,
    )
    assert maintenance.main(
        [
            "--root",
            str(tmp_path),
            "backfill",
            "--start",
            "2026-08-28",
            "--end",
            "2026-08-28",
            "AAPL",
        ]
    ) == 0


def test_live_loop_success_path_and_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(ROOT / "configs" / "v1.yaml")
    scan = tuple(f"S{i:03d}" for i in range(200))
    selected = tuple(f"S{i:03d}" for i in range(30))
    cohort = CohortResult(
        tuple(
            CohortMember(symbol, index + 1, "core", "selected")
            for index, symbol in enumerate(selected)
        ),
        0,
    )
    scores = tuple(
        BroadScanScore(symbol, 1.0, {}, True, "selected") for symbol in scan
    )
    decision_at = datetime(2026, 8, 28, 16, 5, tzinfo=UTC)

    class Collector:
        store = object()

        def prepare(self, symbols):
            assert len(symbols) == 202
            return ()

        def collect(self, symbols):
            stored = tuple(SimpleNamespace(symbol=symbol) for symbol in symbols)
            return CollectionResult(stored=stored, failed_symbols=())

    class Store:
        def __init__(self, path):
            self.path = path

        def latest(self):
            return None

    report = SimpleNamespace(
        payload={
            "decision_state": "PRIMARY",
            "decision": "PRIMARY",
            "no_trade_reason": None,
        },
        primary_symbol="S000",
        snapshot_id="snap-1",
    )
    monkeypatch.setattr(live, "load_config", lambda _: config)
    monkeypatch.setattr(live, "load_scan_universe", lambda *_args, **_kwargs: scan)
    monkeypatch.setattr(
        live,
        "load_universe_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(symbols=scan),
    )
    monkeypatch.setattr(live, "build_default_collector", lambda *_: Collector())
    monkeypatch.setattr(live, "ReportStore", Store)
    monkeypatch.setattr(live, "_regular_session_timestamp", lambda _: True)
    monkeypatch.setattr(live, "_decision_time_reached", lambda *_: True)
    monkeypatch.setattr(live, "select_research_cohort", lambda *a, **k: (cohort, scores))
    monkeypatch.setattr(live, "_refresh_context", lambda *a, **k: (0, decision_at))
    monkeypatch.setattr(live, "run_decision", lambda **k: report)
    monkeypatch.setattr(
        live, "_previous_trading_session", lambda _: date(2026, 8, 28)
    )
    started = []
    monkeypatch.setattr(
        live, "_start_background_backfill", lambda *a, **k: started.append(k)
    )
    monkeypatch.setattr(
        live,
        "_wait_for_next_poll",
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        live.run_live(tmp_path, poll_seconds=1)
    assert started

    monkeypatch.setattr(
        live, "run_live", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    assert live.main(["--root", str(tmp_path), "--poll-seconds", "1"]) == 2
    monkeypatch.setattr(
        live,
        "run_live",
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert live.main(["--root", str(tmp_path), "--poll-seconds", "1"]) == 0


def test_collector_prepare_build_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client:
        def get_markets(self):
            return (
                Market(
                    name="NYSE",
                    startTime="09:30",
                    endTime="16:00",
                    snapQuotesLimit=100,
                ),
            )

        def resolve_symbol(self, symbol):
            if symbol == "BAD":
                raise QuestradeError("bad")
            return SymbolMatch(symbol=symbol, symbolId=1)

    collector = QuestradeCollector(Client(), SimpleNamespace())
    assert collector.markets()[0].name == "NYSE"
    assert collector.prepare([" AAPL ", "BAD"]) == ("BAD",)

    config = load_config(ROOT / "configs" / "v1.yaml")
    made = {}
    monkeypatch.setattr(collector_module, "_load_refresh_token", lambda _: "token")
    monkeypatch.setattr(
        collector_module,
        "TokenStore",
        lambda path: made.setdefault("tokens", path),
    )
    monkeypatch.setattr(
        collector_module,
        "QuestradeClient",
        lambda **kwargs: made.setdefault("client", SimpleNamespace(**kwargs)),
    )
    monkeypatch.setattr(
        collector_module,
        "MarketDataStore",
        lambda path: made.setdefault("store", path),
    )
    built = collector_module.build_default_collector(tmp_path, config)
    assert built.max_latency_ms == config.market_data.max_latency_ms

    record = SimpleNamespace(
        symbol="AAPL",
        last_trade_price=10.0,
        bid_price=9.9,
        ask_price=10.1,
        latency_ms=1,
        is_trade_eligible=True,
        invalid_reason=None,
    )

    class CliCollector:
        def markets(self):
            return (
                Market(
                    name="NYSE",
                    startTime="09:30",
                    endTime="16:00",
                    snapQuotesLimit=100,
                ),
            )

        def collect(self, symbols):
            return CollectionResult(stored=(record,), failed_symbols=())

    monkeypatch.setattr(collector_module, "project_root", lambda: ROOT)
    monkeypatch.setattr(
        collector_module, "build_default_collector", lambda *a: CliCollector()
    )
    assert collector_module.main(["--markets", "AAPL"]) == 0

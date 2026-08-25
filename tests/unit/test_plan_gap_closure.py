from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.market_data.capacity as capacity_module
import day_trading_engine.ops.maintenance as maintenance
from day_trading_engine.market_data.backfill import (
    _manifest_payload,
    _normalize_expected_keys,
    _sessions,
    backfill_one_minute_history,
    write_universe_manifest,
)
from day_trading_engine.market_data.capacity import run_capacity_gate
from day_trading_engine.ops.data_protection import (
    create_backup,
    create_month_end_snapshot,
    restore_backup,
    verify_backup,
)
from day_trading_engine.providers.questrade_history import HistoricalCandle
from day_trading_engine.ui.app import _read_backup_status


class FakeHistoryClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_candles(self, symbol_id: int, **_: object) -> SimpleNamespace:
        self.calls += 1
        candle = HistoricalCandle(
            start=datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
            end=datetime(2026, 1, 5, 14, 31, tzinfo=UTC),
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=100,
        )
        return SimpleNamespace(candles=(candle,))


class FailingHistoryClient:
    def __init__(self, **_: object) -> None:
        pass

    def get_candles(self, symbol_id: int, **_: object) -> SimpleNamespace:
        raise RuntimeError("provider unavailable")


class MissingHistoryClient:
    def __init__(self, **_: object) -> None:
        pass

    def get_candles(self, symbol_id: int, **_: object) -> SimpleNamespace:
        return SimpleNamespace(candles=())


def test_backfill_resumes_completed_sessions(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    manifest = backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert client.calls == 1
    assert payload["entries"][0]["status"] == "complete"
    assert payload["fidelity"] == "BAR_ONLY"
    assert payload["feature_availability"]["historical_bid_ask"] is False

    backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    assert client.calls == 1


def test_backfill_resume_rewrites_current_request_summary(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    manifest = backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    backfill_one_minute_history(
        FailingHistoryClient(),
        symbols={"MSFT": 2},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["coverage"]["current_request_complete"] is False

    resumed = FakeHistoryClient()
    backfill_one_minute_history(
        resumed,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert resumed.calls == 0
    assert payload["coverage"]["current_request_complete"] is True
    assert maintenance._backfill_status(payload) == 0


def test_backfill_refetches_when_symbol_id_changes(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    manifest = backfill_one_minute_history(
        client,
        symbols={"AAPL": 2},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert client.calls == 2
    assert payload["entries"][0]["symbol_id"] == 2


def test_backfill_refetches_corrupted_completed_session(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    manifest = backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    data_file = tmp_path / payload["entries"][0]["files"][0]
    data_file.write_bytes(b"corrupt")

    backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    assert client.calls == 2


def test_backfill_sessions_exclude_market_holidays() -> None:
    sessions = _sessions(date(2026, 12, 24), date(2026, 12, 28))
    assert date(2026, 12, 24) in sessions
    assert date(2026, 12, 25) not in sessions
    assert date(2026, 12, 28) in sessions


def test_legacy_expected_keys_drop_closed_market_dates() -> None:
    keys = {"2025-01-09:AAPL", "2025-01-10:AAPL"}
    assert _normalize_expected_keys(keys) == {"2025-01-10:AAPL"}


def test_coverage_target_requires_contiguous_complete_expected_set() -> None:
    entries = {
        "2025-01-02:AAPL": {"session": "2025-01-02", "status": "complete"},
        "2026-01-02:AAPL": {"session": "2026-01-02", "status": "complete"},
    }
    keys = set(entries)
    payload = _manifest_payload(entries, keys, keys)
    assert payload["coverage"]["span_days"] >= 365
    assert payload["coverage"]["continuous_expected_sessions"] is False
    assert payload["coverage"]["meets_12_month_target"] is False


def test_universe_manifest_labels_survivorship_risk(tmp_path: Path) -> None:
    path = write_universe_manifest({"aapl": 1}, as_of=date(2026, 1, 5), root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbols"] == [{"symbol": "AAPL", "symbol_id": 1}]
    assert "survivorship" in payload["survivorship_risk"]


def test_backup_excludes_tokens_and_restores(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "sample.txt").write_text("research", encoding="utf-8")
    (data / "questrade_tokens.json").write_text("secret", encoding="utf-8")
    backup = create_backup(data, tmp_path / "backups")

    valid, failures = verify_backup(backup)
    assert valid and not failures
    assert not (backup / "data" / "questrade_tokens.json").exists()
    status = json.loads((data / "backup_status.json").read_text(encoding="utf-8"))
    assert "same_volume_as_source" in status

    restored = restore_backup(backup, tmp_path / "restored")
    assert (restored / "sample.txt").read_text(encoding="utf-8") == "research"


def test_backup_uses_portable_paths_and_reads_legacy_windows_paths(tmp_path: Path) -> None:
    data = tmp_path / "data"
    nested = data / "nested"
    nested.mkdir(parents=True)
    (nested / "sample.txt").write_text("research", encoding="utf-8")
    backup = create_backup(data, tmp_path / "backups")
    manifest_path = backup / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "nested/sample.txt" in payload["files"]

    checksum = payload["files"].pop("nested/sample.txt")
    payload["files"]["nested\\sample.txt"] = checksum
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    valid, failures = verify_backup(backup)
    assert valid and not failures
    restored = restore_backup(backup, tmp_path / "legacy-restored")
    assert (restored / "nested" / "sample.txt").read_text(encoding="utf-8") == "research"


def test_backup_rejects_undeclared_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "sample.txt").write_text("research", encoding="utf-8")
    backup = create_backup(data, tmp_path / "backups")
    (backup / "data" / "extra.txt").write_text("not declared", encoding="utf-8")

    valid, failures = verify_backup(backup)
    assert not valid
    assert "undeclared:extra.txt" in failures


def test_backup_rejects_destination_inside_runtime_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    with pytest.raises(ValueError, match="outside"):
        create_backup(data, data / "backups")


def test_month_end_snapshot_metadata_is_verified(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "sample.txt").write_text("research", encoding="utf-8")
    snapshot = create_month_end_snapshot(
        data,
        tmp_path / "backups",
        month="2026-08",
        versions={"algorithm": "a1", "config": "c1", "schema": "s1"},
    )
    payload = json.loads((snapshot / "research_snapshot.json").read_text(encoding="utf-8"))
    assert payload["month"] == "2026-08"
    assert payload["versions"]["algorithm"] == "a1"
    valid, failures = verify_backup(snapshot)
    assert valid and not failures

    (snapshot / "research_snapshot.json").write_text("{}", encoding="utf-8")
    valid, failures = verify_backup(snapshot)
    assert not valid
    assert "checksum-metadata:research_snapshot.json" in failures


def test_capacity_gate_requires_thirty_unique_symbols() -> None:
    with pytest.raises(ValueError, match="at least 30"):
        run_capacity_gate(["AAPL"])


def test_capacity_gate_requires_thirty_valid_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid = [SimpleNamespace(is_trade_eligible=False, latency_ms=1) for _ in range(30)]
    collector = SimpleNamespace(
        quote_batch_size=50,
        collect=lambda symbols: SimpleNamespace(stored=invalid, failed_symbols=()),
    )
    monkeypatch.setattr(capacity_module, "build_default_collector", lambda root: collector)

    report = run_capacity_gate([f"S{i}" for i in range(30)], root=Path("."))
    assert report.stored_quotes == 30
    assert report.valid_quotes == 0
    assert report.passed is False


def test_capacity_cli_handles_setup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        capacity_module,
        "run_capacity_gate",
        lambda symbols: (_ for _ in ()).throw(OSError("no token")),
    )
    assert capacity_module.main([f"S{i}" for i in range(30)]) == 2


def _patch_backfill_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: type[FailingHistoryClient] | type[MissingHistoryClient],
) -> None:
    monkeypatch.setattr(maintenance, "project_root", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "_load_refresh_token", lambda root: "token")
    monkeypatch.setattr(maintenance, "TokenStore", lambda path: object())
    monkeypatch.setattr(maintenance, "QuestradeHistoryClient", client)


def test_backfill_cli_returns_nonzero_for_failed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backfill_cli(tmp_path, monkeypatch, FailingHistoryClient)
    result = maintenance.main(
        ["backfill", "--start", "2026-01-05", "--end", "2026-01-05", "AAPL=1"]
    )
    assert result == 2


def test_backfill_cli_distinguishes_missing_provider_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backfill_cli(tmp_path, monkeypatch, MissingHistoryClient)
    result = maintenance.main(
        ["backfill", "--start", "2026-01-05", "--end", "2026-01-05", "AAPL=1"]
    )
    assert result == 3


def test_backfill_cli_handles_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(maintenance, "project_root", lambda: tmp_path)
    monkeypatch.setattr(
        maintenance,
        "_load_refresh_token",
        lambda root: (_ for _ in ()).throw(OSError("token unreadable")),
    )
    assert maintenance.main(
        ["backfill", "--start", "2026-01-05", "--end", "2026-01-05", "AAPL=1"]
    ) == 2


def test_backup_status_reader_rejects_non_object_json(tmp_path: Path) -> None:
    status = tmp_path / "backup_status.json"
    status.write_text("null", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        _read_backup_status(status)


def test_maintenance_wrappers_pin_project_environment() -> None:
    root = Path(__file__).resolve().parents[2]
    ps1_files = ["backup.ps1", "restore.ps1", "month-end.ps1", "backfill.ps1", "capacity-gate.ps1"]
    sh_files = ["backup.sh", "restore.sh", "month-end.sh", "backfill.sh", "capacity-gate.sh"]
    for name in ps1_files:
        content = (root / name).read_text(encoding="utf-8")
        assert ".venv\\Scripts\\python.exe" in content
        assert "$root" in content
    for name in sh_files:
        content = (root / name).read_text(encoding="utf-8")
        assert ".venv/bin/python" in content
        assert "BASH_SOURCE[0]" in content


def test_scheduled_backup_wrappers_pin_context_and_quote_destination() -> None:
    root = Path(__file__).resolve().parents[2]
    scheduler = (root / "schedule-backup.sh").read_text(encoding="utf-8")
    windows_scheduler = (root / "schedule-backup.ps1").read_text(encoding="utf-8")
    assert "printf -v destination_q '%q'" in scheduler
    assert 'destination="$PWD/$destination"' in scheduler
    assert "command=${command//%/" in scheduler
    assert "[System.IO.Path]::GetFullPath($Destination)" in windows_scheduler

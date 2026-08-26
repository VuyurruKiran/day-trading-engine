from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import day_trading_engine.market_data.backfill as backfill_module
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


def _continuous_candles(start: datetime, end: datetime) -> tuple[HistoricalCandle, ...]:
    candles: list[HistoricalCandle] = []
    current = start
    while current < end:
        candles.append(
            HistoricalCandle(
                start=current,
                end=current + timedelta(minutes=1),
                open=10,
                high=11,
                low=9,
                close=10.5,
                volume=100,
            )
        )
        current += timedelta(minutes=1)
    return tuple(candles)


class FakeHistoryClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_candles(self, symbol_id: int, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        start = kwargs["start"]
        end = kwargs["end"]
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        return SimpleNamespace(candles=_continuous_candles(start, end))


class PartialHistoryClient(FakeHistoryClient):
    def get_candles(self, symbol_id: int, **kwargs: object) -> SimpleNamespace:
        batch = super().get_candles(symbol_id, **kwargs)
        return SimpleNamespace(candles=batch.candles[:-1])


class GapHistoryClient(FakeHistoryClient):
    def __init__(self, missing_indices: set[int]) -> None:
        super().__init__()
        self.missing_indices = missing_indices

    def get_candles(self, symbol_id: int, **kwargs: object) -> SimpleNamespace:
        batch = super().get_candles(symbol_id, **kwargs)
        candles = [
            candle
            for index, candle in enumerate(batch.candles)
            if index not in self.missing_indices
        ]
        return SimpleNamespace(candles=tuple(candles))


class DiscontinuousHistoryClient(FakeHistoryClient):
    def get_candles(self, symbol_id: int, **kwargs: object) -> SimpleNamespace:
        batch = super().get_candles(symbol_id, **kwargs)
        candles = list(batch.candles)
        broken = candles[120]
        candles[120] = HistoricalCandle(
            start=broken.start + timedelta(minutes=1),
            end=broken.end + timedelta(minutes=1),
            open=broken.open,
            high=broken.high,
            low=broken.low,
            close=broken.close,
            volume=broken.volume,
        )
        return SimpleNamespace(candles=tuple(candles))


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
    assert payload["entries"][0]["rows"] == 390
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


def test_backfill_accepts_verified_provider_gap(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        GapHistoryClient({257}),
        symbols={"META": 1},
        start=date(2025, 9, 12),
        end=date(2025, 9, 12),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["status"] == "accepted_gap"
    assert entry["rows"] == 389
    assert entry["reason"] == "provider missing minute"
    assert entry["missing_minutes"] == ["2025-09-12T13:47:00-04:00"]
    assert payload["coverage"]["accepted_gap"] == 1
    assert payload["coverage"]["complete"] == 0
    assert payload["coverage"]["incomplete"] == 0
    assert payload["coverage"]["current_request_complete"] is True


def test_backfill_rejects_large_provider_gap(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        GapHistoryClient(set(range(100, 120))),
        symbols={"META": 1},
        start=date(2025, 9, 12),
        end=date(2025, 9, 12),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    assert entry["status"] == "incomplete"
    assert len(entry["missing_minutes"]) == 20
    assert payload["coverage"]["accepted_gap"] == 0
    assert payload["coverage"]["current_request_complete"] is False


def test_backfill_rejects_discontinuous_session(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        DiscontinuousHistoryClient(),
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["entries"][0]["status"] == "incomplete"
    assert payload["entries"][0]["reason"] == "provider returned duplicate minute candles"


def test_backfill_accepts_july_3_early_close_session(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        FakeHistoryClient(),
        symbols={"AAPL": 1},
        start=date(2025, 7, 3),
        end=date(2025, 7, 3),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["entries"][0]["status"] == "complete"
    assert payload["entries"][0]["rows"] == 210


def test_backfill_accepts_post_thanksgiving_early_close_session(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        FakeHistoryClient(),
        symbols={"AAPL": 1},
        start=date(2026, 11, 27),
        end=date(2026, 11, 27),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["entries"][0]["status"] == "complete"
    assert payload["entries"][0]["rows"] == 210


def test_backfill_accepts_christmas_eve_early_close_session(tmp_path: Path) -> None:
    manifest = backfill_one_minute_history(
        FakeHistoryClient(),
        symbols={"AAPL": 1},
        start=date(2025, 12, 24),
        end=date(2025, 12, 24),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["entries"][0]["status"] == "complete"
    assert payload["entries"][0]["rows"] == 210


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


def test_backfill_skips_exceptional_full_market_closure(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    manifest = backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2025, 1, 9),
        end=date(2025, 1, 9),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert client.calls == 0
    assert payload["entries"] == []
    assert payload["coverage"]["expected"] == 0


def test_backfill_prunes_stale_expected_keys(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    backfill_one_minute_history(
        client,
        symbols={"AAPL": 1, "MSFT": 2},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )

    manifest = backfill_one_minute_history(
        client,
        symbols={"AAPL": 1},
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["expected_keys"] == ["2026-01-05:AAPL"]
    assert payload["coverage"]["expected"] == 1


def test_backfill_refetches_when_symbol_id_changes(tmp_path: Path) -> None:
    client = FakeHistoryClient()
    backfill_one_minute_history(
        client,
        symbols=["AAPL"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    manifest = backfill_one_minute_history(
        client,
        symbols=["AAPL"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        root=tmp_path,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert client.calls == 1
    assert payload["entries"][0]["provider_symbol_id"] is None


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
    payload = _manifest_payload(
        entries,
        keys,
        keys,
        request_start=date(2025, 1, 2),
        request_end=date(2026, 1, 2),
    )
    assert payload["coverage"]["span_days"] >= 365
    assert payload["coverage"]["continuous_expected_sessions"] is False
    assert payload["coverage"]["meets_12_month_target"] is False


def test_coverage_target_uses_calendar_months_for_24_month_gate() -> None:
    entries = {"2024-01-02:AAPL": {"session": "2024-01-02", "status": "complete"}}
    keys = set(entries)
    original = backfill_module._required_keys
    backfill_module._required_keys = lambda expected_keys: set(expected_keys)
    try:
        payload = _manifest_payload(
            entries,
            keys,
            keys,
            request_start=date(2024, 1, 2),
            request_end=date(2026, 1, 1),
        )
        assert payload["coverage"]["meets_24_month_preferred_target"] is False

        payload = _manifest_payload(
            entries,
            keys,
            keys,
            request_start=date(2024, 1, 2),
            request_end=date(2026, 1, 2),
        )
        assert payload["coverage"]["meets_24_month_preferred_target"] is True
    finally:
        backfill_module._required_keys = original


def test_coverage_target_records_accepted_gap() -> None:
    entries = {
        "2024-01-02:AAPL": {
            "session": "2024-01-02",
            "symbol": "AAPL",
            "status": "accepted_gap",
            "rows": 389,
            "missing_minutes": ["2024-01-02T13:47:00-05:00"],
            "reason": "provider missing minute",
        }
    }
    keys = set(entries)
    original = backfill_module._required_keys
    backfill_module._required_keys = lambda expected_keys: set(expected_keys)
    try:
        payload = _manifest_payload(
            entries,
            keys,
            keys,
            request_start=date(2024, 1, 2),
            request_end=date(2026, 1, 2),
        )
        assert payload["coverage"]["accepted_gap"] == 1
        assert payload["coverage"]["accepted_gap_entries"] == [
            {
                "key": "2024-01-02:AAPL",
                "symbol": "AAPL",
                "provider": "",
                "provider_symbol_id": None,
                "session": "2024-01-02",
                "status": "accepted_gap",
                "rows": 389,
                "reason": "provider missing minute",
                "missing_minutes": ["2024-01-02T13:47:00-05:00"],
            }
        ]
        assert payload["coverage"]["meets_24_month_preferred_target"] is True
        assert payload["coverage"]["current_request_complete"] is True
    finally:
        backfill_module._required_keys = original


def test_coverage_span_includes_accepted_gap_sessions() -> None:
    entries = {
        "2024-01-02:AAPL": {"session": "2024-01-02", "status": "accepted_gap"},
        "2024-01-03:AAPL": {"session": "2024-01-03", "status": "complete"},
    }
    keys = set(entries)
    original = backfill_module._required_keys
    backfill_module._required_keys = lambda expected_keys: set(expected_keys)
    try:
        payload = _manifest_payload(
            entries,
            keys,
            keys,
            request_start=date(2024, 1, 2),
            request_end=date(2026, 1, 2),
        )
        assert payload["coverage"]["first_complete_session"] == "2024-01-02"
        assert payload["coverage"]["last_complete_session"] == "2024-01-03"
        assert payload["coverage"]["span_days"] == 1
        assert payload["coverage"]["all_expected_covered"] is True
        assert payload["coverage"]["current_request_complete"] is True
        assert payload["coverage"]["meets_12_month_target"] is True
        assert payload["coverage"]["meets_24_month_preferred_target"] is True
    finally:
        backfill_module._required_keys = original


def test_universe_manifest_labels_survivorship_risk(tmp_path: Path) -> None:
    path = write_universe_manifest(["aapl"], as_of=date(2026, 1, 5), root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbols"] == [
        {"symbol": "AAPL", "provider": "alpaca", "provider_symbol_id": None}
    ]
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


def test_backup_rejects_external_file_symlink(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    link = data / "external-link.txt"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("file symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="symbolic links"):
        create_backup(data, tmp_path / "backups")


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
    monkeypatch.setattr(maintenance, "AlpacaHistoryClient", client)


def _patch_bootstrap_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def fake_write_universe_manifest(*args: object, **kwargs: object) -> Path:
        calls.append((args, kwargs))
        return tmp_path / "data" / "historical" / "universe" / "2026-01-05.json"

    monkeypatch.setattr(maintenance, "project_root", lambda: tmp_path)
    monkeypatch.setattr(maintenance, "write_universe_manifest", fake_write_universe_manifest)
    return calls


def test_backfill_cli_returns_nonzero_for_failed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backfill_cli(tmp_path, monkeypatch, FailingHistoryClient)
    result = maintenance.main(
        ["backfill", "--start", "2026-01-05", "--end", "2026-01-05", "AAPL"]
    )
    assert result == 2


def test_backfill_cli_distinguishes_missing_provider_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backfill_cli(tmp_path, monkeypatch, MissingHistoryClient)
    result = maintenance.main(
        ["backfill", "--start", "2026-01-05", "--end", "2026-01-05", "AAPL"]
    )
    assert result == 3


def test_backfill_cli_handles_setup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(maintenance, "project_root", lambda: tmp_path)
    monkeypatch.setattr(
        maintenance,
        "AlpacaHistoryClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("credentials unreadable")),
    )
    assert maintenance.main(
        ["backfill", "--start", "2026-01-05", "--end", "2026-01-05", "AAPL"]
    ) == 2


def test_bootstrap_universe_cli_writes_ticker_only_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_bootstrap_cli(tmp_path, monkeypatch)
    result = maintenance.main(
        ["bootstrap-universe", "--as-of", "2026-01-05", "AAPL", "MSFT", "NVDA"]
    )

    assert result == 0
    assert calls == [
        (
            (["AAPL", "MSFT", "NVDA"],),
            {
                "as_of": date(2026, 1, 5),
                "root": tmp_path / "data" / "historical",
                "provider": "alpaca",
            },
        )
    ]


@pytest.mark.parametrize(
    ("function_name", "argv", "error"),
    [
        ("create_backup", ["backup", "backup-dir"], OSError("disk unavailable")),
        (
            "restore_backup",
            ["restore", "backup-dir", "restore-dir"],
            ValueError("corrupt manifest"),
        ),
        (
            "create_month_end_snapshot",
            [
                "snapshot",
                "backup-dir",
                "--month",
                "2026-07",
                "--algorithm",
                "a1",
                "--config-version",
                "c1",
                "--schema",
                "s1",
            ],
            sqlite3.OperationalError("database locked"),
        ),
    ],
)
def test_maintenance_protection_commands_return_controlled_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    function_name: str,
    argv: list[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(maintenance, "project_root", lambda: tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(maintenance, function_name, fail)
    assert maintenance.main(argv) == 2
    assert f"{argv[0]} failed: {error}" in capsys.readouterr().out


def test_backup_status_reader_rejects_non_object_json(tmp_path: Path) -> None:
    status = tmp_path / "backup_status.json"
    status.write_text("null", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        _read_backup_status(status)


def test_maintenance_wrappers_pin_project_environment() -> None:
    root = Path(__file__).resolve().parents[2]
    ps1_files = [
        "backup.ps1",
        "restore.ps1",
        "month-end.ps1",
        "bootstrap-universe.ps1",
        "backfill.ps1",
        "capacity-gate.ps1",
    ]
    sh_files = [
        "backup.sh",
        "restore.sh",
        "month-end.sh",
        "bootstrap-universe.sh",
        "backfill.sh",
        "capacity-gate.sh",
    ]
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


def test_scheduled_backup_shell_wrapper_is_executable() -> None:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        pytest.skip("Git index is unavailable")
    result = subprocess.run(
        ["git", "ls-files", "--stage", "schedule-backup.sh"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.split()[0] == "100755"

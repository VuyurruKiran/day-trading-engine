import json
from datetime import date
from pathlib import Path

from day_trading_engine.engine.universe import (
    UniverseCandidate,
    load_universe_snapshot,
    select_research_universe,
    write_universe_snapshot,
)


def _snapshot(effective_from: date):
    return select_research_universe(
        [
            UniverseCandidate(
                symbol="TEST",
                security_id="questrade:1",
                exchange="NASDAQ",
                asset_type="common_stock",
                sector="Technology",
                price=10,
                median_dollar_volume=1_000_000,
                spread_pct=0.001,
                volatility=0.02,
                coverage_ratio=1,
            )
        ],
        effective_from=effective_from,
        target=1,
        cash_usd=100,
        max_spread_pct=0.02,
        min_coverage_ratio=0.9,
        max_sector_fraction=1,
        ipo_seasoning_sessions=20,
        selector_version="test",
        config_version="test",
    )


def test_rebuilt_snapshot_stays_usable_after_earlier_same_month_corruption(
    tmp_path: Path,
) -> None:
    previous = date(2026, 8, 28)
    corrupt_effective = date(2026, 9, 1)
    rebuild_day = date(2026, 9, 2)
    later_session = date(2026, 9, 3)
    write_universe_snapshot(tmp_path, _snapshot(previous))
    corrupt_path = tmp_path / "US-2026-09-corrupt.json"
    corrupt_path.write_text(
        json.dumps({"effective_from": corrupt_effective.isoformat(), "checksum": "bad"}),
        encoding="utf-8",
    )

    assert load_universe_snapshot(tmp_path, as_of=rebuild_day, ignore_invalid=True) is None

    replacement = _snapshot(rebuild_day)
    write_universe_snapshot(tmp_path, replacement)
    loaded = load_universe_snapshot(tmp_path, as_of=later_session, ignore_invalid=True)

    assert not corrupt_path.exists()
    assert corrupt_path.with_name(f"{corrupt_path.name}.invalid").exists()
    assert loaded is not None
    assert loaded.checksum == replacement.checksum


def test_overflowing_snapshot_is_recovered_and_quarantined(tmp_path: Path) -> None:
    previous = date(2026, 8, 28)
    current = date(2026, 9, 1)
    write_universe_snapshot(tmp_path, _snapshot(previous))
    overflow_path = tmp_path / "US-2026-09-overflow.json"
    overflow_path.write_text(
        '{"universe_id":"US-2026-09-overflow","effective_from":"2026-09-01",'
        '"selector_version":"test","config_version":"test","target":1e309,'
        '"members":[],"exclusions":[],"created_at":"2026-09-01T12:00:00+00:00",'
        '"checksum":"bad"}',
        encoding="utf-8",
    )

    assert load_universe_snapshot(tmp_path, as_of=current, ignore_invalid=True) is None

    replacement = _snapshot(current)
    write_universe_snapshot(tmp_path, replacement)
    loaded = load_universe_snapshot(
        tmp_path, as_of=date(2026, 9, 2), ignore_invalid=True
    )

    assert not overflow_path.exists()
    assert overflow_path.with_name(f"{overflow_path.name}.invalid").exists()
    assert loaded is not None
    assert loaded.checksum == replacement.checksum


def test_future_effective_date_tamper_triggers_recovery_and_quarantine(
    tmp_path: Path,
) -> None:
    previous = date(2026, 8, 28)
    current = date(2026, 9, 1)
    write_universe_snapshot(tmp_path, _snapshot(previous))
    current_path = write_universe_snapshot(tmp_path, _snapshot(current))
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload["effective_from"] = "2026-10-01"
    tampered_path = tmp_path / "US-2026-09-future.json"
    tampered_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path.unlink()

    assert load_universe_snapshot(tmp_path, as_of=current, ignore_invalid=True) is None

    replacement = _snapshot(current)
    write_universe_snapshot(tmp_path, replacement)
    loaded = load_universe_snapshot(
        tmp_path, as_of=date(2026, 9, 2), ignore_invalid=True
    )

    assert not tampered_path.exists()
    assert tampered_path.with_name(f"{tampered_path.name}.invalid").exists()
    assert loaded is not None
    assert loaded.checksum == replacement.checksum

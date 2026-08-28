from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.core.config import load_config
from day_trading_engine.core.health import run_health_check
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.discovery import load_scan_universe
from day_trading_engine.market_data.backfill import _sessions, write_universe_manifest
from day_trading_engine.market_data.concurrent_backfill import (
    backfill_one_minute_history_concurrent,
)
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.ops.data_protection import create_backup, create_month_end_snapshot
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient, AlpacaHistoryError

_EASTERN = ZoneInfo("America/New_York")


def latest_completed_session(today: date) -> date:
    """Return the most recent US-equity session strictly before today."""
    sessions = _sessions(today - timedelta(days=10), today - timedelta(days=1))
    if not sessions:
        raise RuntimeError("no completed market session found")
    return sessions[-1]


def _history(root: Path) -> int:
    config = load_config(root / "configs" / "v1.yaml")
    symbols = list(load_scan_universe(root, config))
    session = latest_completed_session(datetime.now(_EASTERN).date())
    data_root = root / "data" / "historical"
    client = AlpacaHistoryClient(symbols=symbols, root=root)
    write_universe_manifest(
        symbols,
        as_of=session,
        root=data_root,
        provider=getattr(client, "provider", "alpaca"),
    )
    manifest = backfill_one_minute_history_concurrent(
        client,
        symbols=symbols,
        start=session,
        end=session,
        root=data_root,
        workers=4,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    coverage = payload.get("coverage", {}) if isinstance(payload, dict) else {}
    return 0 if isinstance(coverage, dict) and coverage.get("current_request_complete") else 2


def _after_close(root: Path, retention_days: int) -> int:
    store = MarketDataStore(root / "data" / "trading.db")
    deleted = store.delete_before(datetime.now(UTC) - timedelta(days=retention_days))
    if deleted:
        store.vacuum()
    print(f"Deleted {deleted} expired trading.db quote rows")
    return 0


def _quality(root: Path) -> int:
    report, _ = run_health_check(root / "configs" / "v1.yaml")
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0 if report.ok else 2


def _backup(root: Path, destination: Path) -> int:
    target = create_backup(root / "data", destination)
    print(target)
    return 0


def _snapshot(root: Path, destination: Path) -> int:
    now = datetime.now(ZoneInfo("America/Edmonton"))
    tomorrow = now.date() + timedelta(days=1)
    if tomorrow.month == now.month:
        print("Not calendar month end; snapshot skipped")
        return 0
    config = load_config(root / "configs" / "v1.yaml")
    target = create_month_end_snapshot(
        root / "data",
        destination,
        month=now.strftime("%Y-%m"),
        versions={
            "algorithm": config.strategy.family,
            "config": config.project.plan_version,
            "schema": "1",
        },
    )
    print(target)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scheduled local maintenance jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("history")
    after_close = commands.add_parser("after-close")
    after_close.add_argument("--retention-days", type=int, default=30)
    commands.add_parser("quality")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    root = project_root()

    try:
        if args.command == "history":
            return _history(root)
        if args.command == "after-close":
            if args.retention_days < 1:
                parser.error("--retention-days must be at least 1")
            return _after_close(root, args.retention_days)
        if args.command == "quality":
            return _quality(root)
        if args.command == "backup":
            return _backup(root, args.destination)
        return _snapshot(root, args.destination)
    except (
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        sqlite3.Error,
        AlpacaHistoryError,
    ) as exc:
        print(f"{args.command} failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

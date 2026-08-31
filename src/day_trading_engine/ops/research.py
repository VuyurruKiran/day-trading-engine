from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from day_trading_engine.core.config import load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.universe import load_universe_snapshot
from day_trading_engine.engine.universe_ledger import UniverseLedger
from day_trading_engine.market_data.backfill import write_universe_manifest
from day_trading_engine.market_data.concurrent_backfill import (
    backfill_one_minute_history_concurrent,
)
from day_trading_engine.ops.data_protection import restore_backup
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient, AlpacaHistoryError
from day_trading_engine.research.cycle import generate_monthly_report


def _months_before(value: date, months: int) -> date:
    if months < 1:
        raise ValueError("months must be positive")
    index = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def sync_universe(root: Path, as_of: date) -> int:
    snapshot = load_universe_snapshot(
        root / "data" / "historical" / "universe", as_of=as_of
    )
    if snapshot is None:
        raise ValueError("no versioned universe snapshot exists for requested date")
    UniverseLedger(root / "data" / "universe.db").record_snapshot(snapshot)
    return len(snapshot.members)


def backfill_active_universe(root: Path, as_of: date, months: int) -> int:
    config = load_config(root / "configs" / "v1.yaml")
    snapshot = load_universe_snapshot(
        root / "data" / "historical" / "universe", as_of=as_of
    )
    if snapshot is None:
        raise ValueError("no active universe snapshot")
    symbols = list(
        dict.fromkeys((*snapshot.symbols, *config.research_universe.benchmark_symbols))
    )
    start = _months_before(as_of, months)
    data_root = root / "data" / "historical"
    client = AlpacaHistoryClient(symbols=symbols, root=root)
    write_universe_manifest(
        symbols,
        as_of=as_of,
        root=data_root,
        provider=getattr(client, "provider", "alpaca"),
    )
    manifest_path = backfill_one_minute_history_concurrent(
        client,
        symbols=symbols,
        start=start,
        end=as_of,
        root=data_root,
        workers=4,
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    coverage = payload.get("coverage", {}) if isinstance(payload, dict) else {}
    return 0 if isinstance(coverage, dict) and coverage.get("current_request_complete") else 2


def restore_drill(backup: Path) -> int:
    """Verify restore plus SQLite/Parquet readability without touching live data."""
    with tempfile.TemporaryDirectory(prefix="day-trading-restore-") as temporary:
        destination = Path(temporary) / "data"
        restore_backup(backup, destination)
        for database in destination.rglob("*.db"):
            with sqlite3.connect(database) as db:
                result = db.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise ValueError(f"restore drill SQLite check failed: {database.name}")
        for parquet in destination.rglob("*.parquet"):
            pd.read_parquet(parquet).head(1)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan v3.1 research operations")
    parser.add_argument("--root", type=Path, default=project_root())
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync-universe")
    sync.add_argument("--as-of", type=date.fromisoformat, required=True)

    backfill = commands.add_parser("backfill-active-universe")
    backfill.add_argument("--as-of", type=date.fromisoformat, required=True)
    backfill.add_argument("--months", type=int, default=24)

    report = commands.add_parser("monthly-report")
    report.add_argument("--month", required=True)

    drill = commands.add_parser("restore-drill")
    drill.add_argument("backup", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "sync-universe":
            print(f"Recorded {sync_universe(args.root, args.as_of)} universe members")
            return 0
        if args.command == "backfill-active-universe":
            return backfill_active_universe(args.root, args.as_of, args.months)
        if args.command == "monthly-report":
            print(generate_monthly_report(args.root, args.month))
            return 0
        return restore_drill(args.backup)
    except (
        AlpacaHistoryError,
        json.JSONDecodeError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        print(f"{args.command} failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

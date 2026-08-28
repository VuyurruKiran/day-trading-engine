from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from day_trading_engine.core.paths import project_root
from day_trading_engine.market_data.backfill import write_universe_manifest
from day_trading_engine.market_data.concurrent_backfill import (
    backfill_one_minute_history_concurrent,
)
from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.ops.data_protection import (
    create_backup,
    create_month_end_snapshot,
    restore_backup,
)
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient, AlpacaHistoryError


def _symbols(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbols must be non-empty")
        if "=" in symbol:
            raise ValueError("symbols must be bare tickers")
        if symbol in seen:
            raise ValueError(f"duplicate symbol {symbol}")
        seen.add(symbol)
        result.append(symbol)
    return result


def _backfill_status(payload: dict[str, object]) -> int:
    entries = payload.get("entries")
    coverage = payload.get("coverage")
    current_keys = payload.get("current_request_keys")
    if not isinstance(entries, list) or not isinstance(coverage, dict):
        return 2
    if not isinstance(current_keys, list) or not all(isinstance(key, str) for key in current_keys):
        return 2
    requested = set(current_keys)
    current_entries = [
        item
        for item in entries
        if isinstance(item, dict) and item.get("key") in requested
    ]
    if any(item.get("status") == "failed" for item in current_entries):
        return 2
    if not coverage.get("current_request_complete"):
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research-data maintenance commands")
    parser.add_argument("--root", type=Path, default=project_root())
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)

    restore = commands.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--verify-only", action="store_true")

    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("destination", type=Path)
    snapshot.add_argument("--month", required=True)
    snapshot.add_argument("--algorithm", required=True)
    snapshot.add_argument("--config-version", required=True)
    snapshot.add_argument("--schema", required=True)

    cleanup = commands.add_parser("cleanup-trading-db")
    cleanup.add_argument("--days", type=int, default=30)

    bootstrap = commands.add_parser("bootstrap-universe")
    bootstrap.add_argument("--as-of", type=date.fromisoformat, required=True)
    bootstrap.add_argument("symbols", nargs="+", help="SYMBOL ...")

    backfill = commands.add_parser("backfill")
    backfill.add_argument("--start", type=date.fromisoformat, required=True)
    backfill.add_argument("--end", type=date.fromisoformat, required=True)
    backfill.add_argument("--universe-as-of", type=date.fromisoformat)
    backfill.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent Alpaca requests (default: 4, maximum: 8)",
    )
    backfill.add_argument("symbols", nargs="+", help="SYMBOL ...")

    args = parser.parse_args(argv)
    root = args.root
    if args.command in {"backup", "restore", "snapshot"}:
        try:
            if args.command == "backup":
                target = create_backup(root / "data", args.destination)
                manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
                print(target)
                if manifest["same_volume_as_source"]:
                    print("WARNING: backup is on the same storage volume as runtime data")
                return 0
            if args.command == "restore":
                restore_backup(args.backup, args.destination, verify_only=args.verify_only)
                print("verified" if args.verify_only else args.destination)
                return 0
            target = create_month_end_snapshot(
                root / "data",
                args.destination,
                month=args.month,
                versions={
                    "algorithm": args.algorithm,
                    "config": args.config_version,
                    "schema": args.schema,
                },
            )
            print(target)
            return 0
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            print(f"{args.command} failed: {exc}")
            return 2

    if args.command == "cleanup-trading-db":
        if args.days < 1:
            parser.error("--days must be at least 1")
        try:
            store = MarketDataStore(root / "data" / "trading.db")
            deleted = store.delete_before(datetime.now(UTC) - timedelta(days=args.days))
            if deleted:
                store.vacuum()
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"cleanup-trading-db failed: {exc}")
            return 2
        print(f"Deleted {deleted} expired trading.db quote rows")
        return 0

    if args.command == "bootstrap-universe":
        try:
            symbols = _symbols(args.symbols)
            manifest_path = write_universe_manifest(
                symbols,
                as_of=args.as_of,
                root=root / "data" / "historical",
                provider="alpaca",
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"bootstrap-universe failed: {exc}")
            return 2
        print(manifest_path)
        return 0

    try:
        symbols = _symbols(args.symbols)
    except ValueError as exc:
        parser.error(str(exc))
    data_root = root / "data" / "historical"
    try:
        client = AlpacaHistoryClient(symbols=symbols, root=root)
        write_universe_manifest(
            symbols,
            as_of=args.universe_as_of or args.start,
            root=data_root,
            provider=getattr(client, "provider", "alpaca"),
        )
        manifest_path = backfill_one_minute_history_concurrent(
            client,
            symbols=symbols,
            start=args.start,
            end=args.end,
            root=data_root,
            workers=args.workers,
        )
    except (OSError, ValueError, AlpacaHistoryError) as exc:
        print(f"Alpaca backfill failed: {exc}")
        return 2

    print(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return 2
    return _backfill_status(payload)


if __name__ == "__main__":
    raise SystemExit(main())

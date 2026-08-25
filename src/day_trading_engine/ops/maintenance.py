from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path

from day_trading_engine.core.paths import project_root
from day_trading_engine.market_data.backfill import (
    backfill_one_minute_history,
    write_universe_manifest,
)
from day_trading_engine.market_data.collector import (
    _load_refresh_token,
)
from day_trading_engine.ops.data_protection import (
    create_backup,
    create_month_end_snapshot,
    restore_backup,
)
from day_trading_engine.providers.alpaca_history import AlpacaHistoryClient, AlpacaHistoryError
from day_trading_engine.providers.questrade import TokenStore as _TokenStore

QuestradeHistoryClient = AlpacaHistoryClient
TokenStore = _TokenStore


def _symbols(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        symbol, separator, raw_id = value.partition("=")
        if not separator or not symbol.strip():
            raise ValueError("symbols must use SYMBOL=ID")
        symbol_id = int(raw_id)
        if symbol_id <= 0:
            raise ValueError("symbol ids must be positive")
        result[symbol.strip().upper()] = symbol_id
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

    backfill = commands.add_parser("backfill")
    backfill.add_argument("--start", type=date.fromisoformat, required=True)
    backfill.add_argument("--end", type=date.fromisoformat, required=True)
    backfill.add_argument("symbols", nargs="+", help="SYMBOL=ID")

    args = parser.parse_args(argv)
    root = project_root()
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

    try:
        symbols = _symbols(args.symbols)
    except ValueError as exc:
        parser.error(str(exc))
    data_root = root / "data" / "historical"
    try:
        _load_refresh_token(root)
        client = QuestradeHistoryClient(symbols=symbols, root=root)
        write_universe_manifest(symbols, as_of=args.start, root=data_root)
        manifest_path = backfill_one_minute_history(
            client,
            symbols=symbols,
            start=args.start,
            end=args.end,
            root=data_root,
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

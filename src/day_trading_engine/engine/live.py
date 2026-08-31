from __future__ import annotations

import argparse
import calendar
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.context.collector import collect_public_context
from day_trading_engine.context.store import ContextStore
from day_trading_engine.core.config import load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.discovery import load_scan_universe, select_research_symbols
from day_trading_engine.engine.runner import _regular_session_timestamp, run_decision
from day_trading_engine.features.context import CONTEXT_FEATURE_VERSION
from day_trading_engine.market_data.backfill import _sessions
from day_trading_engine.market_data.collector import build_default_collector
from day_trading_engine.providers.questrade import QuestradeError
from day_trading_engine.ui.state import ReportStore

_POLL_SECONDS = 60
_EASTERN = ZoneInfo("America/New_York")


def _wait_for_next_poll(deadline: float, poll_seconds: int) -> float:
    """Wait only until the next monotonic polling deadline."""
    deadline += poll_seconds
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
        return deadline
    return time.monotonic()


def _history_start(end: date, months: int) -> date:
    """Return the calendar date exactly ``months`` before ``end`` when possible."""
    if months < 1:
        raise ValueError("historical backfill months must be at least 1")
    month_index = end.year * 12 + end.month - 1 - months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    day = min(end.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _previous_trading_session(as_of: date) -> date:
    """Return the latest NYSE session strictly before ``as_of``."""
    sessions = _sessions(as_of - timedelta(days=7), as_of - timedelta(days=1))
    if not sessions:
        raise RuntimeError("unable to resolve previous trading session")
    return sessions[-1]


def _decision_time_reached(config, now: datetime) -> bool:
    """Return whether the configured local decision time has been reached."""
    local = now.astimezone(ZoneInfo(config.project.timezone))
    hour, minute = (int(part) for part in config.project.decision_time.split(":"))
    return (local.hour, local.minute) >= (hour, minute)


def _start_background_backfill(
    root: Path,
    symbols: tuple[str, ...],
    *,
    end: date,
    as_of: date,
    months: int,
) -> subprocess.Popen[bytes]:
    """Launch the existing resumable Alpaca backfill without blocking live decisions."""
    start = _history_start(end, months)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "day_trading_engine.ops.maintenance",
            "--root",
            str(root),
            "backfill",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--universe-as-of",
            as_of.isoformat(),
            *symbols,
        ],
        cwd=root,
    )


def _refresh_context(
    root: Path,
    symbols: tuple[str, ...],
    *,
    software_version: str,
) -> tuple[int, datetime]:
    """Collect and persist optional public context before the decision is ranked."""
    result = collect_public_context(symbols)
    completed_at = datetime.now(UTC)
    with ContextStore(root / "data" / "context.db") as store:
        added = store.add_many(result.records)
        store.record_collection(
            run_at=completed_at,
            record_count=len(result.records),
            errors=result.errors,
            versions={
                "context_feature": CONTEXT_FEATURE_VERSION,
                "software": software_version,
            },
        )
    if result.errors:
        print(f"Context collection degraded: {'; '.join(result.errors)}")
    return added, completed_at


def run_live(root: Path, *, poll_seconds: int = _POLL_SECONDS) -> int:
    """Scan the versioned research universe while collecting benchmarks separately."""
    config = load_config(root / "configs" / "v1.yaml")
    scan_universe = load_scan_universe(root, config)
    benchmark_symbols = config.research_universe.benchmark_symbols
    collection_symbols = tuple(dict.fromkeys((*scan_universe, *benchmark_symbols)))
    scan_symbols = set(scan_universe)
    collector = build_default_collector(root, config)
    report_store = ReportStore(root / "data" / "decision_state.db")
    latest = report_store.latest()
    decided_session = None if latest is None else latest.payload.get("session")
    attempted_context_keys: set[tuple[str, frozenset[str]]] = set()
    frozen_cohort: tuple[str, tuple[str, ...]] | None = None
    deadline = time.monotonic()

    try:
        failed = collector.prepare(list(collection_symbols))
    except QuestradeError as exc:
        print(f"Questrade symbol preparation failed: {exc}")
    else:
        if failed:
            raise RuntimeError(f"unresolved scan/benchmark symbols: {', '.join(failed)}")

    while True:
        now = datetime.now(UTC)
        if _regular_session_timestamp(now):
            try:
                result = collector.collect(list(collection_symbols))
            except QuestradeError as exc:
                print(f"Questrade collection failed: {exc}")
            else:
                print(
                    f"Collected {len(result.stored)}/{len(collection_symbols)} "
                    "research/benchmark quotes"
                )
                if result.failed_symbols:
                    unresolved = ", ".join(result.failed_symbols)
                    raise RuntimeError(f"unresolved scan/benchmark symbols: {unresolved}")

                decision_now = datetime.now(UTC)
                decision_date = decision_now.astimezone(_EASTERN).date()
                session = decision_date.isoformat()
                if decided_session != session and _decision_time_reached(config, decision_now):
                    scan_quotes = tuple(
                        row
                        for row in result.stored
                        if str(getattr(row, "symbol", row)).upper() in scan_symbols
                    )
                    if frozen_cohort is not None and frozen_cohort[0] == session:
                        selected = frozen_cohort[1]
                    else:
                        selected = select_research_symbols(
                            scan_quotes,
                            config=config,
                            session_key=session,
                        )
                    target = config.research.daily_candidate_count
                    if len(selected) < target:
                        print(
                            "Decision not ready: scan produced "
                            f"{len(selected)}/{target} candidates"
                        )
                    else:
                        if frozen_cohort is None or frozen_cohort[0] != session:
                            frozen_cohort = (session, tuple(selected))
                            selected = frozen_cohort[1]
                        context_key = (session, frozenset(selected))
                        if context_key not in attempted_context_keys:
                            attempted_context_keys.add(context_key)
                            try:
                                _, decision_now = _refresh_context(
                                    root,
                                    selected,
                                    software_version=config.project.software_version,
                                )
                            except (OSError, sqlite3.Error, ValueError) as exc:
                                print(f"Context collection degraded: {exc}")
                                decision_now = datetime.now(UTC)
                        else:
                            decision_now = datetime.now(UTC)
                        decision_config = config.model_copy(
                            update={
                                "market_data": config.market_data.model_copy(
                                    update={"watchlist": selected}
                                )
                            }
                        )
                        try:
                            report = run_decision(
                                config=decision_config,
                                market_store=collector.store,
                                report_store=report_store,
                                created_at=decision_now,
                            )
                        except (RuntimeError, ValueError, sqlite3.Error) as exc:
                            print(f"Decision not ready: {exc}")
                        else:
                            if report.payload.get("decision_state") == "DATA_NOT_READY":
                                print(
                                    "Decision not ready: complete current-session "
                                    "inputs unavailable"
                                )
                            else:
                                history_end = _previous_trading_session(decision_date)
                                try:
                                    _start_background_backfill(
                                        root,
                                        selected,
                                        end=history_end,
                                        as_of=decision_date,
                                        months=(
                                            config.research.historical_bootstrap_months_preferred
                                        ),
                                    )
                                except OSError as exc:
                                    print(f"Historical backfill failed to start: {exc}")
                                else:
                                    print(
                                        "Historical backfill started in background for "
                                        f"{len(selected)} selected symbols"
                                    )

                                decided_session = session
                                outcome = (
                                    report.primary_symbol or report.payload["no_trade_reason"]
                                )
                                print(f"{report.payload['decision']}: {outcome}")
                                print(f"Snapshot: {report.snapshot_id}")

        deadline = _wait_for_next_poll(deadline, poll_seconds)


def main(argv: list[str] | None = None) -> int:
    """Run the live engine command."""
    parser = argparse.ArgumentParser(description="Run the live V1 collector and decision loop")
    parser.add_argument("--root", type=Path, default=project_root())
    parser.add_argument("--poll-seconds", type=int, default=_POLL_SECONDS)
    args = parser.parse_args(argv)
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    try:
        return run_live(args.root, poll_seconds=args.poll_seconds)
    except RuntimeError as exc:
        print(f"Live engine failed: {exc}")
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

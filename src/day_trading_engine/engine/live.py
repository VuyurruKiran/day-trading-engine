from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.core.config import load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.discovery import load_scan_universe, select_research_symbols
from day_trading_engine.engine.runner import _regular_session_timestamp, run_decision
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


def run_live(root: Path, *, poll_seconds: int = _POLL_SECONDS) -> int:
    """Continuously scan the broad US pool and publish one daily 30-symbol decision."""
    config = load_config(root / "configs" / "v1.yaml")
    scan_universe = load_scan_universe(root, config)
    collector = build_default_collector(root, config)
    report_store = ReportStore(root / "data" / "decision_state.db")
    latest = report_store.latest()
    decided_session = None if latest is None else latest.payload.get("session")
    deadline = time.monotonic()

    try:
        failed = collector.prepare(list(scan_universe))
    except QuestradeError as exc:
        print(f"Questrade symbol preparation failed: {exc}")
    else:
        if failed:
            print(f"Unresolved scan symbols: {', '.join(failed)}")

    while True:
        now = datetime.now(UTC)
        if _regular_session_timestamp(now):
            try:
                result = collector.collect(list(scan_universe))
            except QuestradeError as exc:
                print(f"Questrade collection failed: {exc}")
            else:
                stored_count = len(result.stored)
                print(f"Collected {stored_count}/{len(scan_universe)} broad-scan quotes")
                if result.failed_symbols:
                    print(f"Failed symbols: {', '.join(result.failed_symbols)}")

                decision_now = datetime.now(UTC)
                session = decision_now.astimezone(_EASTERN).date().isoformat()
                if decided_session != session:
                    selected = select_research_symbols(
                        result.stored,
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
                        except (RuntimeError, ValueError) as exc:
                            print(f"Decision not ready: {exc}")
                        else:
                            if report.payload.get("decision_state") == "DATA_NOT_READY":
                                print(
                                    "Decision not ready: complete current-session "
                                    "inputs unavailable"
                                )
                            else:
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
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

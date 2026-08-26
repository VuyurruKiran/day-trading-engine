from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from day_trading_engine.core.config import load_config
from day_trading_engine.core.paths import project_root
from day_trading_engine.engine.runner import _regular_session_timestamp, run_decision
from day_trading_engine.market_data.collector import build_default_collector
from day_trading_engine.ui.state import ReportStore

_POLL_SECONDS = 60


def run_live(root: Path, *, poll_seconds: int = _POLL_SECONDS) -> int:
    """Continuously collect the configured research universe and publish one daily decision."""
    config = load_config(root / "configs" / "v1.yaml")
    collector = build_default_collector(root, config)
    report_store = ReportStore(root / "data" / "decision_state.db")
    decided_session: str | None = None

    while True:
        now = datetime.now(UTC)
        if _regular_session_timestamp(now):
            result = collector.collect(list(config.market_data.watchlist))
            print(f"Collected {len(result.stored)}/{len(config.market_data.watchlist)} live quotes")
            if result.failed_symbols:
                print(f"Failed symbols: {', '.join(result.failed_symbols)}")

            session = now.astimezone().date().isoformat()
            if decided_session != session:
                try:
                    report = run_decision(
                        config=config,
                        market_store=collector.store,
                        report_store=report_store,
                        created_at=now,
                    )
                except RuntimeError as exc:
                    print(f"Decision not ready: {exc}")
                else:
                    decided_session = session
                    outcome = report.primary_symbol or report.payload["no_trade_reason"]
                    print(f"{report.payload['decision']}: {outcome}")
                    print(f"Snapshot: {report.snapshot_id}")
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
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

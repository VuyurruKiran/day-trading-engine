from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

import pandas as pd

from day_trading_engine.engine.domain import TradePlan
from day_trading_engine.paper.replay import ReplayBar, evaluate_plan


def load_replay_bars(
    root: Path,
    *,
    symbol: str,
    session: str,
    snapshot_at: datetime,
) -> list[ReplayBar]:
    """Load stored Alpaca one-minute bars at/after the immutable decision cutoff."""
    target = (
        root
        / "interval=OneMinute"
        / f"date={session}"
        / f"symbol={symbol.upper()}"
        / "candles.parquet"
    )
    if not target.exists():
        return []
    frame = pd.read_parquet(target)
    required = {"start", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("historical outcome data is missing required candle columns")
    frame = frame.copy()
    frame["start"] = pd.to_datetime(frame["start"], utc=True, errors="raise")
    cutoff = pd.Timestamp(snapshot_at.astimezone(UTC))
    frame = frame.loc[frame["start"] >= cutoff].sort_values("start", kind="stable")
    return [
        ReplayBar(
            row.start.to_pydatetime(),
            float(row.high),
            float(row.low),
            float(row.close),
        )
        for row in frame.itertuples()
    ]


def evaluate_shadow_outcome(
    plan: dict[str, object] | None,
    bars: list[ReplayBar] | tuple[ReplayBar, ...],
    *,
    snapshot_at: datetime,
    unavailable_reason: str | None = None,
) -> dict[str, object]:
    """Evaluate one ledger-neutral BAR_ONLY outcome with the shared replay engine."""
    if snapshot_at.tzinfo is None or snapshot_at.utcoffset() is None:
        raise ValueError("snapshot_at must be timezone-aware")
    if plan is None:
        return {
            "status": "unavailable",
            "reason": unavailable_reason or "candidate had no eligible trade plan",
            "fidelity": "BAR_ONLY",
        }
    ordered = list(bars)
    if not ordered:
        return {
            "status": "unavailable",
            "reason": "no stored post-decision Alpaca one-minute bars",
            "fidelity": "BAR_ONLY",
        }
    try:
        entry = float(plan["entry"])
        stop = float(plan["stop"])
        target = float(plan["target"])
        quantity = int(plan["quantity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("shadow plan is incomplete") from exc
    valid_prices = all(
        isfinite(value) and value > 0 for value in (entry, stop, target)
    )
    if not valid_prices or quantity < 1:
        raise ValueError("shadow plan prices/quantity are invalid")
    risk = entry - stop
    if risk <= 0:
        raise ValueError("shadow plan stop must be below entry")
    if target <= entry:
        raise ValueError("shadow plan target must be above entry")
    research_plan = TradePlan(
        symbol=str(plan.get("symbol", "")).upper(),
        entry=entry,
        stop=stop,
        target=target,
        quantity=quantity,
        max_loss=quantity * risk,
        valid_from=snapshot_at,
        expires_at=ordered[-1].ts,
    )
    outcome = evaluate_plan(research_plan, ordered)
    return {
        "status": "complete",
        "triggered": outcome.triggered,
        "outcome": outcome.outcome,
        "mfe": outcome.mfe,
        "mae": outcome.mae,
        "exit_price": outcome.exit_price,
        "exit_at": None if outcome.exit_at is None else outcome.exit_at.isoformat(),
        "fidelity": "BAR_ONLY",
    }

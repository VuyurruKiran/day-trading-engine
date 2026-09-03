from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
        / "provider=alpaca"
        / "feed=sip"
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
    if "session_phase" not in frame.columns:
        return []
    frame = frame.loc[frame["session_phase"] == "REGULAR"]
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


def _first_at_or_after(bars: list[ReplayBar], at: datetime) -> ReplayBar | None:
    return next((bar for bar in bars if bar.ts >= at), None)


def _reference_returns(
    bars: list[ReplayBar], *, baseline: float, start_at: datetime
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for minutes in (5, 15, 30, 60):
        bar = _first_at_or_after(bars, start_at + timedelta(minutes=minutes))
        result[f"{minutes}m"] = None if bar is None else (bar.close - baseline) / baseline
    result["eod"] = (bars[-1].close - baseline) / baseline if bars else None
    return result


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
    valid_prices = all(isfinite(value) and value > 0 for value in (entry, stop, target))
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
    entry_bar = next(
        (bar for bar in ordered if bar.ts >= snapshot_at and bar.high >= entry), None
    )
    active = (
        ordered if entry_bar is None else [bar for bar in ordered if bar.ts >= entry_bar.ts]
    )
    favorable = max(active, key=lambda bar: bar.high)
    adverse = min(active, key=lambda bar: bar.low)

    triggered_bars = [] if entry_bar is None else active
    target_at = next((bar.ts for bar in triggered_bars if bar.high >= target), None)
    stop_at = next((bar.ts for bar in triggered_bars if bar.low <= stop), None)
    exit_price = outcome.exit_price
    shadow_return = (
        None if not outcome.triggered or exit_price is None else (exit_price - entry) / entry
    )
    start_at = snapshot_at if entry_bar is None else entry_bar.ts
    baseline = ordered[0].close if entry_bar is None else entry
    time_to_target = (
        None
        if entry_bar is None or target_at is None
        else (target_at - entry_bar.ts).total_seconds()
    )
    time_to_stop = (
        None
        if entry_bar is None or stop_at is None
        else (stop_at - entry_bar.ts).total_seconds()
    )

    return {
        "status": "complete",
        "triggered": outcome.triggered,
        "entry_triggered": outcome.triggered,
        "target_hit": target_at is not None,
        "stop_hit": stop_at is not None,
        "target_before_stop": outcome.outcome == "target_before_stop",
        "stop_before_target": outcome.outcome == "stop_before_target",
        "no_trigger": not outcome.triggered,
        "outcome": outcome.outcome,
        "mfe": outcome.mfe,
        "mae": outcome.mae,
        "mfe_pct": outcome.mfe / entry,
        "mae_pct": outcome.mae / entry,
        "max_favorable_price": favorable.high,
        "max_favorable_at": favorable.ts.isoformat(),
        "max_adverse_price": adverse.low,
        "max_adverse_at": adverse.ts.isoformat(),
        "entry_at": None if entry_bar is None else entry_bar.ts.isoformat(),
        "target_at": None if target_at is None else target_at.isoformat(),
        "stop_at": None if stop_at is None else stop_at.isoformat(),
        "time_to_entry_seconds": (
            None if entry_bar is None else (entry_bar.ts - snapshot_at).total_seconds()
        ),
        "time_to_target_seconds": time_to_target,
        "time_to_stop_seconds": time_to_stop,
        "setup_expired": not outcome.triggered,
        "reference_returns": _reference_returns(ordered, baseline=baseline, start_at=start_at),
        "shadow_return": shadow_return,
        "shadow_pnl": None if shadow_return is None else quantity * entry * shadow_return,
        "exit_price": exit_price,
        "exit_at": None if outcome.exit_at is None else outcome.exit_at.isoformat(),
        "fidelity": "BAR_ONLY",
    }

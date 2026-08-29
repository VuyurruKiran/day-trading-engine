from __future__ import annotations

from datetime import datetime
from math import isfinite

from day_trading_engine.market_data.store import StoredQuote, parse_timestamp


def evaluate_shadow_outcome(
    plan: dict[str, object] | None,
    quotes: tuple[StoredQuote, ...],
    *,
    snapshot_at: datetime,
    unavailable_reason: str | None = None,
) -> dict[str, object]:
    """Evaluate one research-only plan from chronologically stored live quote snapshots."""
    if snapshot_at.tzinfo is None or snapshot_at.utcoffset() is None:
        raise ValueError("snapshot_at must be timezone-aware")
    if plan is None:
        return {
            "status": "unavailable",
            "reason": unavailable_reason or "candidate had no eligible trade plan",
            "fidelity": "QUOTE_AWARE",
        }

    try:
        entry = float(plan["entry"])
        stop = float(plan["stop"])
        target = float(plan["target"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("shadow plan is incomplete") from exc
    if not all(isfinite(value) and value > 0 for value in (entry, stop, target)):
        raise ValueError("shadow plan prices must be finite and positive")

    points = [
        (parse_timestamp(quote.received_at), float(quote.last_trade_price))
        for quote in quotes
        if quote.is_trade_eligible
        and quote.last_trade_price is not None
        and parse_timestamp(quote.received_at) >= snapshot_at
    ]
    points.sort(key=lambda item: item[0])
    if not points:
        return {
            "status": "unavailable",
            "reason": "no post-decision trade-eligible quote snapshots",
            "fidelity": "QUOTE_AWARE",
        }

    triggered = False
    mfe = 0.0
    mae = 0.0
    for at, price in points:
        if not triggered and price >= entry:
            triggered = True
        if not triggered:
            continue
        mfe = max(mfe, price - entry)
        mae = max(mae, entry - price)
        if price <= stop:
            return _complete("stop", stop, at, mfe, mae)
        if price >= target:
            return _complete("target", target, at, mfe, mae)

    last_at, last_price = points[-1]
    if not triggered:
        return {
            "status": "complete",
            "triggered": False,
            "outcome": "no_trigger",
            "mfe": 0.0,
            "mae": 0.0,
            "exit_price": None,
            "exit_at": None,
            "fidelity": "QUOTE_AWARE",
        }
    return _complete("eod", last_price, last_at, mfe, mae)


def _complete(
    outcome: str,
    exit_price: float,
    exit_at: datetime,
    mfe: float,
    mae: float,
) -> dict[str, object]:
    return {
        "status": "complete",
        "triggered": True,
        "outcome": outcome,
        "mfe": mfe,
        "mae": mae,
        "exit_price": exit_price,
        "exit_at": exit_at.isoformat(),
        "fidelity": "QUOTE_AWARE",
    }

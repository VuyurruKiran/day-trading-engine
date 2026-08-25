from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite

from day_trading_engine.engine.domain import TradePlan

from .ledger import PaperLedger


class ReplayFidelity(StrEnum):
    BAR_ONLY = "BAR_ONLY"
    QUOTE_AWARE = "QUOTE_AWARE"
    CONTEXT_AWARE = "CONTEXT_AWARE"
    FORWARD_LIVE = "FORWARD_LIVE"


@dataclass(frozen=True)
class ReplayBar:
    ts: datetime
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise ValueError("replay bar timestamp must be timezone-aware")
        if any(not isfinite(value) or value <= 0 for value in (self.high, self.low, self.close)):
            raise ValueError("replay bar prices must be finite and positive")
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("replay bar prices are inconsistent")


@dataclass(frozen=True)
class ShadowOutcome:
    triggered: bool
    outcome: str
    mfe: float
    mae: float
    exit_price: float | None
    exit_at: datetime | None = None


def _validate_order(bars: list[ReplayBar]) -> None:
    if any(left.ts >= right.ts for left, right in zip(bars, bars[1:])):
        raise ValueError("replay bars must be strictly chronological")


def evaluate_plan(plan: TradePlan, bars: list[ReplayBar]) -> ShadowOutcome:
    _validate_order(bars)
    triggered = False
    mfe = mae = 0.0
    for bar in bars:
        if bar.ts < plan.valid_from:
            continue
        if bar.ts > plan.expires_at and not triggered:
            break
        newly_triggered = not triggered and bar.high >= plan.entry
        if newly_triggered:
            triggered = True
            if bar.low <= plan.stop:
                return ShadowOutcome(True, "ambiguous_same_bar", mfe, mae, None, bar.ts)
        if not triggered:
            continue
        mfe = max(mfe, bar.high - plan.entry)
        mae = max(mae, plan.entry - bar.low)
        hit_stop, hit_target = bar.low <= plan.stop, bar.high >= plan.target
        if hit_stop and hit_target:
            return ShadowOutcome(True, "ambiguous_same_bar", mfe, mae, None, bar.ts)
        if hit_stop:
            return ShadowOutcome(True, "stop_before_target", mfe, mae, plan.stop, bar.ts)
        if hit_target:
            return ShadowOutcome(True, "target_before_stop", mfe, mae, plan.target, bar.ts)
    return ShadowOutcome(
        triggered,
        "eod" if triggered else "no_trigger",
        mfe,
        mae,
        bars[-1].close if triggered and bars else None,
        bars[-1].ts if triggered and bars else None,
    )


def apply_actual_trade(
    ledger: PaperLedger, plan: TradePlan, bars: list[ReplayBar]
) -> ShadowOutcome:
    outcome = evaluate_plan(plan, bars)
    if not outcome.triggered:
        return outcome
    if outcome.outcome == "ambiguous_same_bar":
        raise ValueError("same-bar ordering requires higher-fidelity data")
    entry_ts = next(bar.ts for bar in bars if bar.ts >= plan.valid_from and bar.high >= plan.entry)
    ledger.buy(plan.symbol, plan.quantity, plan.entry, entry_ts)
    exit_price = outcome.exit_price if outcome.exit_price is not None else bars[-1].close
    exit_at = outcome.exit_at if outcome.exit_at is not None else bars[-1].ts
    ledger.sell(plan.symbol, exit_price, exit_at)
    return outcome


def replay_session(
    plans: dict[str, TradePlan | None],
    bars_by_symbol: dict[str, list[ReplayBar]],
    *,
    primary_symbol: str | None,
    ledger: PaperLedger,
) -> dict[str, ShadowOutcome | None]:
    outcomes: dict[str, ShadowOutcome | None] = {}
    for symbol, plan in plans.items():
        bars = bars_by_symbol.get(symbol, [])
        outcomes[symbol] = evaluate_plan(plan, bars) if plan is not None and bars else None
    if primary_symbol is None:
        return outcomes
    primary = plans.get(primary_symbol)
    bars = bars_by_symbol.get(primary_symbol, [])
    if primary is None or not bars:
        return outcomes
    outcomes[primary_symbol] = apply_actual_trade(ledger, primary, bars)
    return outcomes

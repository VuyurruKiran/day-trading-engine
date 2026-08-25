from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

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


@dataclass(frozen=True)
class ShadowOutcome:
    triggered: bool
    outcome: str
    mfe: float
    mae: float
    exit_price: float | None


def evaluate_plan(plan: TradePlan, bars: list[ReplayBar]) -> ShadowOutcome:
    triggered = False
    mfe = mae = 0.0
    for bar in bars:
        if bar.ts > plan.expires_at and not triggered:
            break
        if not triggered and bar.high >= plan.entry:
            triggered = True
        if not triggered:
            continue
        mfe = max(mfe, bar.high - plan.entry)
        mae = max(mae, plan.entry - bar.low)
        hit_stop, hit_target = bar.low <= plan.stop, bar.high >= plan.target
        if hit_stop and hit_target:
            return ShadowOutcome(True, "ambiguous_same_bar", mfe, mae, None)
        if hit_stop:
            return ShadowOutcome(True, "stop_before_target", mfe, mae, plan.stop)
        if hit_target:
            return ShadowOutcome(True, "target_before_stop", mfe, mae, plan.target)
    return ShadowOutcome(
        triggered,
        "eod" if triggered else "no_trigger",
        mfe,
        mae,
        bars[-1].close if triggered and bars else None,
    )


def apply_actual_trade(
    ledger: PaperLedger, plan: TradePlan, bars: list[ReplayBar]
) -> ShadowOutcome:
    outcome = evaluate_plan(plan, bars)
    if not outcome.triggered:
        return outcome
    if outcome.outcome == "ambiguous_same_bar":
        raise ValueError("same-bar stop/target ordering requires higher-fidelity data")
    entry_ts = next(bar.ts for bar in bars if bar.high >= plan.entry)
    ledger.buy(plan.symbol, plan.quantity, plan.entry, entry_ts)
    exit_price = outcome.exit_price if outcome.exit_price is not None else bars[-1].close
    ledger.sell(plan.symbol, exit_price, bars[-1].ts)
    return outcome


def replay_session(
    plans: dict[str, TradePlan | None],
    bars_by_symbol: dict[str, list[ReplayBar]],
    *,
    primary_symbol: str | None,
    ledger: PaperLedger,
) -> dict[str, ShadowOutcome | None]:
    """Evaluate every research plan, but mutate the ledger only for the primary plan."""
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

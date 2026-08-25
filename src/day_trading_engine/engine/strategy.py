from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class StrategyPolicy:
    max_spread_pct: float
    max_volatility: float
    min_rvol: float
    min_volume: int
    entry_buffer_pct: float
    stop_buffer_pct: float
    reward_to_risk: float


@dataclass(frozen=True)
class CandidateSnapshot:
    symbol: str
    price: float
    bid: float
    ask: float
    volume: int
    rvol: float
    volatility: float
    vwap: float
    opening_range_high: float
    market_relative_strength: float = 0.0
    sector_relative_strength: float = 0.0
    fresh: bool = True
    delayed: bool = False
    halted: bool = False


@dataclass(frozen=True)
class CandidateEvaluation:
    symbol: str
    eligible: bool
    score: float | None
    reason: str


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    status: str
    score: float
    entry: float
    stop: float
    target: float
    quantity: int
    expiry: str


@dataclass(frozen=True)
class DecisionResult:
    research: tuple[CandidateEvaluation, ...]
    finalists: tuple[TradePlan, ...]
    primary: TradePlan | None
    no_trade_reason: str | None


def evaluate_baseline(
    cohort: list[CandidateSnapshot] | tuple[CandidateSnapshot, ...],
    *,
    cash_usd: float,
    active_positions: int,
    policy: StrategyPolicy,
    kill_switch: bool = False,
    final_min: int = 2,
    final_max: int = 5,
) -> DecisionResult:
    """Evaluate the transparent opening-range/VWAP continuation baseline."""
    if not isfinite(cash_usd) or cash_usd <= 0:
        raise ValueError("cash_usd must be positive")
    if active_positions < 0:
        raise ValueError("active_positions cannot be negative")
    if not 2 <= final_min <= final_max <= 5:
        raise ValueError("finalist bounds must satisfy 2 <= min <= max <= 5")
    if kill_switch or active_positions:
        reason = (
            "global kill switch is active"
            if kill_switch
            else "active V1 position already exists"
        )
        research = tuple(
            CandidateEvaluation(row.symbol.upper(), False, None, reason) for row in cohort
        )
        return DecisionResult(research, (), None, reason)

    research: list[CandidateEvaluation] = []
    plans: list[TradePlan] = []
    for row in cohort:
        reason = _hard_gate_reason(row, cash_usd, policy)
        if reason is not None:
            research.append(CandidateEvaluation(row.symbol.upper(), False, None, reason))
            continue

        entry = max(row.opening_range_high, row.vwap) * (1 + policy.entry_buffer_pct)
        stop = min(row.opening_range_high, row.vwap) * (1 - policy.stop_buffer_pct)
        risk = entry - stop
        quantity = int(cash_usd // entry)
        if risk <= 0 or quantity < 1:
            reason = "invalid risk geometry or insufficient cash"
            research.append(CandidateEvaluation(row.symbol.upper(), False, None, reason))
            continue

        score = _technical_score(row)
        target = entry + (risk * policy.reward_to_risk)
        status = "ENTRY_VALID" if row.price >= entry else "WAIT"
        symbol = row.symbol.upper()
        research.append(CandidateEvaluation(symbol, True, score, "passed hard gates"))
        plans.append(
            TradePlan(
                symbol=symbol,
                status=status,
                score=score,
                entry=entry,
                stop=stop,
                target=target,
                quantity=quantity,
                expiry="END_OF_DAY",
            )
        )

    plans.sort(key=lambda item: (-item.score, item.symbol))
    finalists = tuple(plans[:final_max])
    if len(finalists) < final_min:
        return DecisionResult(
            tuple(research), (), None, "fewer than minimum trade-eligible finalists"
        )
    return DecisionResult(tuple(research), finalists, finalists[0], None)


def _hard_gate_reason(
    row: CandidateSnapshot, cash_usd: float, policy: StrategyPolicy
) -> str | None:
    values = (
        row.price,
        row.bid,
        row.ask,
        row.rvol,
        row.volatility,
        row.vwap,
        row.opening_range_high,
        row.volume,
        row.market_relative_strength,
        row.sector_relative_strength,
    )
    try:
        finite = all(isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        finite = False
    if not finite:
        return "non-finite market input"
    if not row.fresh:
        return "stale market data"
    if row.delayed:
        return "delayed market data"
    if row.halted:
        return "trading halt"
    if min(row.price, row.bid, row.ask, row.vwap, row.opening_range_high) <= 0:
        return "non-positive market price"
    if row.ask < row.bid:
        return "crossed market"
    midpoint = (row.ask + row.bid) / 2
    spread_pct = (row.ask - row.bid) / midpoint
    if spread_pct > policy.max_spread_pct:
        return "spread exceeds limit"
    if row.volatility > policy.max_volatility:
        return "volatility exceeds limit"
    if row.rvol < policy.min_rvol:
        return "relative volume below limit"
    if row.volume < policy.min_volume:
        return "liquidity below limit"
    if max(row.opening_range_high, row.vwap) * (1 + policy.entry_buffer_pct) > cash_usd:
        return "entry does not fit cash-only account"
    return None


def _technical_score(row: CandidateSnapshot) -> float:
    vwap_distance = (row.price / row.vwap) - 1
    return round(
        (vwap_distance * 100)
        + (row.rvol - 1)
        + row.market_relative_strength
        + row.sector_relative_strength,
        10,
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import floor, isfinite

from .domain import CandidateDecision, CandidateInput
from .domain import TradePlan as ResearchTradePlan


@dataclass(frozen=True)
class StrategyPolicy:
    max_spread_pct: float
    max_volatility: float
    min_rvol: float
    min_volume: int
    entry_buffer_pct: float
    stop_buffer_pct: float
    reward_to_risk: float

    def __post_init__(self) -> None:
        values = (
            self.max_spread_pct,
            self.max_volatility,
            self.min_rvol,
            self.min_volume,
            self.entry_buffer_pct,
            self.stop_buffer_pct,
            self.reward_to_risk,
        )
        if any(not isfinite(float(value)) for value in values):
            raise ValueError("strategy policy values must be finite")
        if min(values) < 0 or self.reward_to_risk <= 0:
            raise ValueError(
                "strategy policy values must be non-negative with positive reward/risk"
            )


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


@dataclass(frozen=True)
class RiskPolicy:
    max_spread_pct: float = 0.01
    max_volatility: float = 0.08
    min_volume: float = 100_000
    min_rvol: float = 1.0
    max_risk_usd: float = 1.0
    reward_risk: float = 2.0
    setup_minutes: int = 30

    def __post_init__(self) -> None:
        values = (
            self.max_spread_pct,
            self.max_volatility,
            self.min_volume,
            self.min_rvol,
            self.max_risk_usd,
            self.reward_risk,
        )
        if any(not isfinite(float(value)) for value in values):
            raise ValueError("risk policy values must be finite")
        if min(values[:5]) < 0 or self.reward_risk <= 0 or self.setup_minutes <= 0:
            raise ValueError("risk policy values are outside valid domains")


def evaluate_candidate(
    c: CandidateInput,
    *,
    cash: float,
    policy: RiskPolicy | None = None,
    active_position: bool = False,
    kill_switch: bool = False,
    require_spread: bool = True,
) -> CandidateDecision:
    policy = policy or RiskPolicy()
    if not isfinite(cash) or cash <= 0:
        raise ValueError("cash must be finite and positive")
    vetoes: list[str] = []
    if kill_switch:
        vetoes.append("global kill switch active")
    if active_position:
        vetoes.append("V1 already has an active position")
    if not c.provider_ok or c.stale or c.delayed:
        vetoes.append("market data is not trustworthy")
    if c.halted:
        vetoes.append("symbol is halted")
    spread = c.spread_pct
    if (spread is None and require_spread) or (
        spread is not None and spread > policy.max_spread_pct
    ):
        vetoes.append("spread unavailable or above limit")
    if c.volatility > policy.max_volatility:
        vetoes.append("volatility above limit")
    if c.volume < policy.min_volume or c.rvol < policy.min_rvol:
        vetoes.append("liquidity/relative volume below limit")
    if c.price > cash:
        vetoes.append("price does not fit current cash")
    if vetoes:
        return CandidateDecision(c.symbol, False, 0.0, tuple(vetoes))
    if not (c.price > c.vwap and c.price >= c.opening_range_high):
        return CandidateDecision(
            c.symbol,
            False,
            0.0,
            ("opening-range/VWAP trigger not valid",),
        )
    stop = max(c.vwap, c.opening_range_low)
    per_share_risk = c.price - stop
    if per_share_risk <= 0:
        return CandidateDecision(c.symbol, False, 0.0, ("non-positive stop distance",))
    qty = floor(min(cash / c.price, policy.max_risk_usd / per_share_risk))
    if qty < 1:
        return CandidateDecision(
            c.symbol,
            False,
            0.0,
            ("risk/cash sizing yields zero shares",),
        )
    technical = min(1.0, max(0.0, (c.rvol - 1) / 3))
    plan = ResearchTradePlan(
        symbol=c.symbol,
        entry=c.price,
        stop=stop,
        target=c.price + policy.reward_risk * per_share_risk,
        quantity=qty,
        max_loss=qty * per_share_risk,
        valid_from=c.as_of,
        expires_at=c.as_of + timedelta(minutes=policy.setup_minutes),
    )
    return CandidateDecision(c.symbol, True, technical, ("ORB/VWAP continuation valid",), plan)


def evaluate_baseline(
    cohort: list[CandidateSnapshot] | tuple[CandidateSnapshot, ...],
    *,
    cash_usd: float,
    active_positions: int,
    policy: StrategyPolicy,
    kill_switch: bool = False,
    final_min: int = 1,
    final_max: int = 5,
) -> DecisionResult:
    """Evaluate the transparent opening-range/VWAP continuation baseline."""
    if not isfinite(cash_usd) or cash_usd <= 0:
        raise ValueError("cash_usd must be positive")
    if active_positions < 0:
        raise ValueError("active_positions cannot be negative")
    if not 1 <= final_min <= final_max <= 5:
        raise ValueError("finalist bounds must satisfy 1 <= min <= max <= 5")
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
        target = entry + risk * policy.reward_to_risk
        status = "ENTRY_VALID" if row.price >= entry else "WAIT"
        symbol = row.symbol.upper()
        research.append(CandidateEvaluation(symbol, True, score, "passed hard gates"))
        plans.append(
            TradePlan(
                symbol,
                status,
                score,
                round(entry, 3),
                round(stop, 3),
                round(target, 3),
                quantity,
                "END_OF_DAY",
            )
        )
    plans.sort(key=lambda item: (-item.score, item.symbol))
    finalists = tuple(plans[:final_max])
    if len(finalists) < final_min:
        return DecisionResult(
            tuple(research),
            (),
            None,
            "fewer than minimum trade-eligible finalists",
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
    if row.volume < 0 or row.rvol < 0 or row.volatility < 0:
        return "negative market measurement"
    if row.ask < row.bid:
        return "crossed market"
    midpoint = (row.ask + row.bid) / 2
    if (row.ask - row.bid) / midpoint > policy.max_spread_pct:
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
    vwap_distance = row.price / row.vwap - 1
    return round(
        vwap_distance * 100
        + (row.rvol - 1)
        + row.market_relative_strength
        + row.sector_relative_strength,
        10,
    )

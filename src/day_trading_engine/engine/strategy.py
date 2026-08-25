from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import floor

from .domain import CandidateDecision, CandidateInput, TradePlan


@dataclass(frozen=True)
class RiskPolicy:
    max_spread_pct: float = 0.01
    max_volatility: float = 0.08
    min_volume: float = 100_000
    min_rvol: float = 1.0
    max_risk_usd: float = 1.0
    reward_risk: float = 2.0
    setup_minutes: int = 30


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
    if c.price <= 0 or c.price > cash:
        vetoes.append("price does not fit current cash")
    if vetoes:
        return CandidateDecision(c.symbol, False, 0.0, tuple(vetoes))

    continuation = c.price > c.vwap and c.price >= c.opening_range_high
    if not continuation:
        return CandidateDecision(c.symbol, False, 0.0, ("opening-range/VWAP trigger not valid",))

    stop = max(c.vwap, c.opening_range_low)
    per_share_risk = c.price - stop
    if per_share_risk <= 0:
        return CandidateDecision(c.symbol, False, 0.0, ("non-positive stop distance",))
    qty = floor(min(cash / c.price, policy.max_risk_usd / per_share_risk))
    if qty < 1:
        return CandidateDecision(c.symbol, False, 0.0, ("risk/cash sizing yields zero shares",))

    technical = min(1.0, max(0.0, (c.rvol - 1) / 3))
    target = c.price + policy.reward_risk * per_share_risk
    plan = TradePlan(
        symbol=c.symbol,
        entry=c.price,
        stop=stop,
        target=target,
        quantity=qty,
        max_loss=qty * per_share_risk,
        valid_from=c.as_of,
        expires_at=c.as_of + timedelta(minutes=policy.setup_minutes),
    )
    return CandidateDecision(c.symbol, True, technical, ("ORB/VWAP continuation valid",), plan)

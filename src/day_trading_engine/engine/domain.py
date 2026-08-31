from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class CohortBucket(StrEnum):
    CORE = "core"
    BOUNDARY = "boundary"
    DIVERSITY = "diversity"


class DecisionStatus(StrEnum):
    WAIT = "WAIT"
    ENTRY_VALID = "ENTRY VALID"
    CANCEL = "CANCEL"
    HOLD = "HOLD"
    EXIT = "EXIT"
    NO_TRADE = "NO TRADE"


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    as_of: datetime
    price: float
    bid: float | None
    ask: float | None
    volume: float
    rvol: float
    vwap: float
    opening_range_high: float
    opening_range_low: float
    volatility: float
    market_score: float | None = None
    news_score: float | None = None
    social_score: float | None = None
    fundamental_score: float | None = None
    stale: bool = False
    delayed: bool = False
    halted: bool = False
    provider_ok: bool = True

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        required = (
            self.price,
            self.volume,
            self.rvol,
            self.vwap,
            self.opening_range_high,
            self.opening_range_low,
            self.volatility,
        )
        optional = (
            self.bid,
            self.ask,
            self.market_score,
            self.news_score,
            self.social_score,
            self.fundamental_score,
        )
        if any(not isfinite(value) for value in required):
            raise ValueError("required market measurements must be finite")
        if any(value is not None and not isfinite(value) for value in optional):
            raise ValueError("optional market measurements must be finite when provided")
        if min(self.price, self.vwap, self.opening_range_high, self.opening_range_low) <= 0:
            raise ValueError("market prices must be positive")
        if self.volume < 0 or self.rvol < 0 or self.volatility < 0:
            raise ValueError("volume, rvol and volatility must be non-negative")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask < self.bid:
            return None
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid if mid else None


@dataclass(frozen=True)
class CohortMember:
    candidate: CandidateInput
    bucket: CohortBucket
    source_rank: int


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    entry: float
    stop: float
    target: float
    quantity: int
    max_loss: float
    valid_from: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CandidateDecision:
    symbol: str
    eligible: bool
    score: float
    reasons: tuple[str, ...]
    plan: TradePlan | None = None

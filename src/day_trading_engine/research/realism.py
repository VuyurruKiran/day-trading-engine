from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite


@dataclass(frozen=True)
class ExecutionProfile:
    commission_per_order: float = 0.0
    slippage_bps: float = 0.0
    manual_latency_seconds: float = 0.0
    fx_rate: float | None = None
    fx_fee_bps: float = 0.0
    fill_ratio: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.commission_per_order,
            self.slippage_bps,
            self.manual_latency_seconds,
            self.fx_fee_bps,
            self.fill_ratio,
        )
        if any(not isfinite(value) for value in values):
            raise ValueError("execution inputs must be finite")
        if min(values[:4]) < 0:
            raise ValueError("execution costs/latency cannot be negative")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be less than 10000")
        if not 0 <= self.fill_ratio <= 1:
            raise ValueError("fill_ratio must be in [0,1]")
        if self.fx_rate is not None and (not isfinite(self.fx_rate) or self.fx_rate <= 0):
            raise ValueError("fx_rate must be finite and positive")
        if self.fx_fee_bps and self.fx_rate is None:
            raise ValueError("fx_rate is required when fx fees are enabled")

    def filled_quantity(self, requested: int) -> int:
        if type(requested) is not int or requested < 0:
            raise ValueError("requested quantity must be a non-negative integer")
        return int(requested * self.fill_ratio)

    def adjusted_buy(self, price: float) -> float:
        return price * (1 + self.slippage_bps / 10_000)

    def adjusted_sell(self, price: float) -> float:
        return price * (1 - self.slippage_bps / 10_000)


@dataclass(frozen=True)
class PriceObservation:
    ts: datetime
    price: float


def manual_fill(
    profile: ExecutionProfile,
    *,
    signal_at: datetime,
    observations: list[PriceObservation],
    side: str,
) -> PriceObservation | None:
    """Select the first observable manual fill after configured click latency."""
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if signal_at.tzinfo is None or signal_at.utcoffset() is None:
        raise ValueError("signal_at must be timezone-aware")
    ready_at = signal_at + timedelta(seconds=profile.manual_latency_seconds)
    eligible = [item for item in observations if item.ts >= ready_at]
    if not eligible:
        return None
    chosen = min(eligible, key=lambda item: item.ts)
    if not isfinite(chosen.price) or chosen.price <= 0:
        raise ValueError("observed fill price must be finite and positive")
    price = (
        profile.adjusted_buy(chosen.price)
        if side == "buy"
        else profile.adjusted_sell(chosen.price)
    )
    return PriceObservation(chosen.ts, price)


@dataclass(frozen=True)
class MarketActivation:
    market: str
    calendar_validated: bool
    currency_model_validated: bool
    entitlement_validated: bool

    @property
    def enabled(self) -> bool:
        if self.market.upper() == "US":
            return True
        return self.market.upper() == "CA" and all(
            (self.calendar_validated, self.currency_model_validated, self.entitlement_validated)
        )


def round_trip_cost(
    profile: ExecutionProfile, *, entry: float, exit: float, quantity: int
) -> float:
    if (
        type(quantity) is not int
        or quantity < 1
        or not isfinite(entry)
        or not isfinite(exit)
        or entry <= 0
        or exit <= 0
    ):
        raise ValueError("invalid execution inputs")
    buy = profile.adjusted_buy(entry)
    sell = profile.adjusted_sell(exit)
    market_cost = (buy - entry + exit - sell) * quantity
    commissions = 2 * profile.commission_per_order
    fx_cost = 0.0
    if profile.fx_rate is not None and profile.fx_fee_bps:
        account_notional = (entry + exit) * quantity * profile.fx_rate
        fx_cost = account_notional * profile.fx_fee_bps / 10_000
    return market_cost + commissions + fx_cost

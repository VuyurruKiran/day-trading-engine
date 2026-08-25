from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionProfile:
    commission_per_order: float = 0.0
    slippage_bps: float = 0.0
    manual_latency_seconds: float = 0.0
    fx_rate: float | None = None
    fx_fee_bps: float = 0.0
    fill_ratio: float = 1.0

    def __post_init__(self) -> None:
        if min(
            self.commission_per_order,
            self.slippage_bps,
            self.manual_latency_seconds,
            self.fx_fee_bps,
        ) < 0:
            raise ValueError("execution costs/latency cannot be negative")
        if not 0 <= self.fill_ratio <= 1:
            raise ValueError("fill_ratio must be in [0,1]")

    def filled_quantity(self, requested: int) -> int:
        if requested < 0:
            raise ValueError("requested quantity cannot be negative")
        return int(requested * self.fill_ratio)

    def adjusted_buy(self, price: float) -> float:
        return price * (1 + self.slippage_bps / 10_000)

    def adjusted_sell(self, price: float) -> float:
        return price * (1 - self.slippage_bps / 10_000)


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
    if quantity < 1 or entry <= 0 or exit <= 0:
        raise ValueError("invalid execution inputs")
    buy = profile.adjusted_buy(entry)
    sell = profile.adjusted_sell(exit)
    market_cost = (buy - entry + exit - sell) * quantity
    commissions = 2 * profile.commission_per_order
    return market_cost + commissions

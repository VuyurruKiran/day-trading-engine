from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    quantity: int
    price: float
    ts: datetime
    commission: float = 0.0
    slippage: float = 0.0


@dataclass
class PaperLedger:
    cash: float = 100.0
    position_symbol: str | None = None
    position_qty: int = 0
    entry_price: float = 0.0
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash != 100.0:
            raise ValueError("V1 paper ledger must start at exactly USD 100.00")

    def buy(self, symbol: str, quantity: int, price: float, ts: datetime) -> Fill:
        if self.position_symbol is not None:
            raise ValueError("V1 allows one active position")
        if quantity < 1 or not isfinite(price) or price <= 0:
            raise ValueError("invalid fill")
        cost = quantity * price
        if cost > self.cash + 1e-9:
            raise ValueError("cash-only ledger cannot leverage")
        fill = Fill(symbol, "BUY", quantity, price, ts)
        self.cash -= cost
        self.position_symbol, self.position_qty, self.entry_price = symbol, quantity, price
        self.fills.append(fill)
        return fill

    def sell(self, symbol: str, price: float, ts: datetime) -> Fill:
        if symbol != self.position_symbol or self.position_qty < 1:
            raise ValueError("no matching open position")
        if not isfinite(price) or price <= 0:
            raise ValueError("invalid fill")
        fill = Fill(symbol, "SELL", self.position_qty, price, ts)
        self.cash += self.position_qty * price
        self.fills.append(fill)
        self.position_symbol, self.position_qty, self.entry_price = None, 0, 0.0
        return fill

    def reconstruct_cash(self, starting_cash: float = 100.0) -> float:
        cash = starting_cash
        for fill in self.fills:
            signed = -1 if fill.side == "BUY" else 1
            cash += signed * fill.quantity * fill.price - fill.commission - fill.slippage
        return cash

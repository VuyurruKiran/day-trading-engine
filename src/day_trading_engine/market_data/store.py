from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from day_trading_engine.providers.questrade import Quote, ResponseMeta


@dataclass(frozen=True)
class StoredQuote:
    symbol: str
    symbol_id: int
    bid_price: float | None
    bid_size: int | None
    ask_price: float | None
    ask_size: int | None
    last_trade_price: float | None
    volume: int | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    delay_seconds: int
    is_halted: bool
    source_at: str
    received_at: str
    source_time_origin: str
    latency_ms: int
    rate_limit_remaining: int | None
    rate_limit_reset: int | None
    is_trade_eligible: bool
    invalid_reason: str | None


class MarketDataStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_quotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    symbol_id INTEGER NOT NULL,
                    bid_price REAL,
                    bid_size INTEGER,
                    ask_price REAL,
                    ask_size INTEGER,
                    last_trade_price REAL,
                    volume INTEGER,
                    open_price REAL,
                    high_price REAL,
                    low_price REAL,
                    delay_seconds INTEGER NOT NULL,
                    is_halted INTEGER NOT NULL,
                    source_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    source_time_origin TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    rate_limit_remaining INTEGER,
                    rate_limit_reset INTEGER,
                    is_trade_eligible INTEGER NOT NULL,
                    invalid_reason TEXT,
                    UNIQUE(symbol_id, received_at)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_time "
                "ON market_quotes(symbol, received_at DESC)"
            )

    def store_quote(
        self,
        quote: Quote,
        meta: ResponseMeta,
        *,
        max_latency_ms: int = 5_000,
    ) -> StoredQuote:
        eligible, reason = evaluate_quote_quality(quote, meta, max_latency_ms=max_latency_ms)
        record = StoredQuote(
            symbol=quote.symbol.upper(),
            symbol_id=quote.symbolId,
            bid_price=quote.bidPrice,
            bid_size=quote.bidSize,
            ask_price=quote.askPrice,
            ask_size=quote.askSize,
            last_trade_price=quote.lastTradePrice,
            volume=quote.volume,
            open_price=quote.openPrice,
            high_price=quote.highPrice,
            low_price=quote.lowPrice,
            delay_seconds=quote.delay,
            is_halted=quote.isHalted,
            source_at=meta.source_at.isoformat(),
            received_at=meta.received_at.isoformat(),
            source_time_origin=meta.source_time_origin,
            latency_ms=meta.latency_ms,
            rate_limit_remaining=meta.rate_limit_remaining,
            rate_limit_reset=meta.rate_limit_reset,
            is_trade_eligible=eligible,
            invalid_reason=reason,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO market_quotes (
                    symbol, symbol_id, bid_price, bid_size, ask_price, ask_size,
                    last_trade_price, volume, open_price, high_price, low_price,
                    delay_seconds, is_halted, source_at, received_at,
                    source_time_origin, latency_ms, rate_limit_remaining,
                    rate_limit_reset, is_trade_eligible, invalid_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.symbol,
                    record.symbol_id,
                    record.bid_price,
                    record.bid_size,
                    record.ask_price,
                    record.ask_size,
                    record.last_trade_price,
                    record.volume,
                    record.open_price,
                    record.high_price,
                    record.low_price,
                    record.delay_seconds,
                    int(record.is_halted),
                    record.source_at,
                    record.received_at,
                    record.source_time_origin,
                    record.latency_ms,
                    record.rate_limit_remaining,
                    record.rate_limit_reset,
                    int(record.is_trade_eligible),
                    record.invalid_reason,
                ),
            )
        return record

    def latest(self, symbol: str) -> StoredQuote | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM market_quotes WHERE symbol = ? ORDER BY received_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        if row is None:
            return None
        return StoredQuote(
            symbol=row["symbol"],
            symbol_id=row["symbol_id"],
            bid_price=row["bid_price"],
            bid_size=row["bid_size"],
            ask_price=row["ask_price"],
            ask_size=row["ask_size"],
            last_trade_price=row["last_trade_price"],
            volume=row["volume"],
            open_price=row["open_price"],
            high_price=row["high_price"],
            low_price=row["low_price"],
            delay_seconds=row["delay_seconds"],
            is_halted=bool(row["is_halted"]),
            source_at=row["source_at"],
            received_at=row["received_at"],
            source_time_origin=row["source_time_origin"],
            latency_ms=row["latency_ms"],
            rate_limit_remaining=row["rate_limit_remaining"],
            rate_limit_reset=row["rate_limit_reset"],
            is_trade_eligible=bool(row["is_trade_eligible"]),
            invalid_reason=row["invalid_reason"],
        )


def evaluate_quote_quality(
    quote: Quote,
    meta: ResponseMeta,
    *,
    max_latency_ms: int,
) -> tuple[bool, str | None]:
    if quote.delay != 0:
        return False, "DELAYED_QUOTE"
    if quote.isHalted:
        return False, "HALTED"
    if meta.source_time_origin != "http_date":
        return False, "SOURCE_TIME_UNVERIFIED"
    if meta.latency_ms > max_latency_ms:
        return False, "STALE_QUOTE"
    if quote.bidPrice is None or quote.askPrice is None or quote.lastTradePrice is None:
        return False, "INCOMPLETE_LEVEL1"
    if quote.bidPrice <= 0 or quote.askPrice <= 0 or quote.lastTradePrice <= 0:
        return False, "INVALID_PRICE"
    if quote.askPrice < quote.bidPrice:
        return False, "CROSSED_MARKET"
    return True, None


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)

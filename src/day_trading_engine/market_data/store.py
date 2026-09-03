from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

from day_trading_engine.providers.questrade import Quote, ResponseMeta

_LIVE_PROVIDER = "questrade"


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
    provider: str = "unknown"
    last_trade_time: str | None = None
    session_phase: str | None = None
    session_date: str | None = None


def _stored_quote(row: sqlite3.Row) -> StoredQuote:
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
        provider=row["provider"],
        last_trade_time=row["last_trade_time"],
        session_phase=row["session_phase"],
        session_date=row["session_date"],
    )


def _monitor_bucket(received_at: str) -> str:
    observed = datetime.fromisoformat(received_at)
    minute = observed.minute - observed.minute % 5
    return observed.replace(minute=minute, second=0, microsecond=0).isoformat()


def _record_refinement_snapshot(connection: sqlite3.Connection, record: StoredQuote) -> None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'research_selections'"
    ).fetchone()
    if table is None:
        return
    session = record.session_date or record.received_at[:10]
    selection = connection.execute(
        """
        SELECT snapshot_id, role, decision_price, entry, stop, target
        FROM research_selections
        WHERE session = ? AND symbol = ?
        ORDER BY final_rank
        LIMIT 1
        """,
        (session, record.symbol),
    ).fetchone()
    if selection is None:
        return

    snapshot_id, role, decision_price, entry, stop, target = selection
    price = record.last_trade_price
    return_pct = None
    if price is not None and isfinite(price) and price > 0 and decision_price > 0:
        return_pct = (price / decision_price - 1.0) * 100.0
    previous = connection.execute(
        """
        SELECT MAX(return_pct), MIN(return_pct)
        FROM research_monitoring
        WHERE snapshot_id = ? AND symbol = ?
        """,
        (snapshot_id, record.symbol),
    ).fetchone()
    previous_mfe, previous_mae = previous if previous is not None else (None, None)
    observed_returns = [0.0, *(value for value in (previous_mfe, return_pct) if value is not None)]
    adverse_returns = [0.0, *(value for value in (previous_mae, return_pct) if value is not None)]
    mfe_pct = max(observed_returns)
    mae_pct = min(adverse_returns)

    # ponytail: five-minute quote snapshots cannot prove a transient intrabucket target/stop
    # touch; upgrade to minute bars if refinement later needs exact path reconstruction.
    target_hit = int(price is not None and target is not None and price >= target)
    stop_hit = int(price is not None and stop is not None and price <= stop)
    payload = {
        "provider": record.provider,
        "source_at": record.source_at,
        "delay_seconds": record.delay_seconds,
        "halted": record.is_halted,
        "invalid_reason": record.invalid_reason,
    }
    connection.execute(
        """
        INSERT OR IGNORE INTO research_monitoring(
            snapshot_id, symbol, role, bucket_at, observed_at, price, bid, ask,
            volume, return_pct, mfe_pct, mae_pct, target_hit, stop_hit,
            quote_eligible, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            record.symbol,
            role,
            _monitor_bucket(record.received_at),
            record.received_at,
            price,
            record.bid_price,
            record.ask_price,
            record.volume,
            return_pct,
            mfe_pct,
            mae_pct,
            target_hit,
            stop_hit,
            int(record.is_trade_eligible),
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        ),
    )


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
        with closing(self._connect()) as connection, connection:
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
                    provider TEXT NOT NULL DEFAULT 'unknown',
                    last_trade_time TEXT,
                    session_phase TEXT,
                    session_date TEXT,
                    UNIQUE(symbol_id, received_at, provider)
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(market_quotes)")}
            if "provider" not in columns:
                connection.execute(
                    "ALTER TABLE market_quotes ADD COLUMN provider TEXT NOT NULL DEFAULT 'unknown'"
                )
            for name in ("last_trade_time", "session_phase", "session_date"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE market_quotes ADD COLUMN {name} TEXT")
            from day_trading_engine.market_data.backfill import _canonical_schedule

            for row_id, received_at in connection.execute(
                "SELECT id, received_at FROM market_quotes WHERE session_date IS NULL"
            ):
                observed = datetime.fromisoformat(received_at)
                eastern_date = observed.astimezone(ZoneInfo("America/New_York")).date()
                try:
                    phase = _canonical_schedule(eastern_date).phase(observed)
                except ValueError:
                    phase = None
                connection.execute(
                    "UPDATE market_quotes SET session_date = ?, session_phase = ? WHERE id = ?",
                    (
                        eastern_date.isoformat(),
                        None if phase is None else phase.value,
                        row_id,
                    ),
                )
            unique_keys = {
                tuple(
                    row[2]
                    for row in connection.execute(f"PRAGMA index_info('{index[1]}')")
                )
                for index in connection.execute("PRAGMA index_list(market_quotes)")
                if index[2]
            }
            if ("symbol_id", "received_at", "provider") not in unique_keys:
                connection.executescript(
                    """
                    CREATE TABLE market_quotes_new (
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
                        provider TEXT NOT NULL DEFAULT 'unknown',
                        last_trade_time TEXT,
                        session_phase TEXT,
                        session_date TEXT,
                        UNIQUE(symbol_id, received_at, provider)
                    );
                    INSERT INTO market_quotes_new
                    SELECT id, symbol, symbol_id, bid_price, bid_size, ask_price, ask_size,
                           last_trade_price, volume, open_price, high_price, low_price,
                           delay_seconds, is_halted, source_at, received_at, source_time_origin,
                           latency_ms, rate_limit_remaining, rate_limit_reset, is_trade_eligible,
                           invalid_reason, provider, last_trade_time, session_phase, session_date
                    FROM market_quotes;
                    DROP TABLE market_quotes;
                    ALTER TABLE market_quotes_new RENAME TO market_quotes;
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
        from day_trading_engine.market_data.backfill import _canonical_schedule

        market_at = quote.lastTradeTime or meta.source_at
        if market_at.tzinfo is None or market_at.utcoffset() is None:
            raise ValueError("quote market timestamp must be timezone-aware")
        eastern_date = market_at.astimezone(ZoneInfo("America/New_York")).date()
        try:
            phase = _canonical_schedule(eastern_date).phase(market_at)
        except ValueError:
            phase = None
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
            provider=_LIVE_PROVIDER,
            last_trade_time=(
                None if quote.lastTradeTime is None else quote.lastTradeTime.isoformat()
            ),
            session_phase=None if phase is None else phase.value,
            session_date=eastern_date.isoformat(),
        )
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO market_quotes (
                    symbol, symbol_id, bid_price, bid_size, ask_price, ask_size,
                    last_trade_price, volume, open_price, high_price, low_price,
                    delay_seconds, is_halted, source_at, received_at,
                    source_time_origin, latency_ms, rate_limit_remaining,
                    rate_limit_reset, is_trade_eligible, invalid_reason, provider,
                    last_trade_time, session_phase, session_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    record.provider,
                    record.last_trade_time,
                    record.session_phase,
                    record.session_date,
                ),
            )
            _record_refinement_snapshot(connection, record)
        return record

    def latest(self, symbol: str) -> StoredQuote | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM market_quotes WHERE symbol = ? AND provider = ? "
                "ORDER BY received_at DESC LIMIT 1",
                (symbol.upper(), _LIVE_PROVIDER),
            ).fetchone()
        return None if row is None else _stored_quote(row)

    def latest_all(self) -> tuple[StoredQuote, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT q.*
                FROM market_quotes AS q
                WHERE q.provider = ? AND q.id = (
                    SELECT q2.id
                    FROM market_quotes AS q2
                    WHERE q2.symbol = q.symbol AND q2.provider = ?
                    ORDER BY q2.received_at DESC, q2.id DESC
                    LIMIT 1
                )
                ORDER BY q.symbol
                """,
                (_LIVE_PROVIDER, _LIVE_PROVIDER),
            ).fetchall()
        return tuple(_stored_quote(row) for row in rows)

    def session(self, symbol: str, session_date: str) -> tuple[StoredQuote, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM market_quotes
                WHERE symbol = ? AND session_date = ? AND provider = ?
                ORDER BY received_at
                """,
                (symbol.upper(), session_date, _LIVE_PROVIDER),
            ).fetchall()
        return tuple(_stored_quote(row) for row in rows)

    def delete_before(self, cutoff: datetime) -> int:
        """Delete live quote rows older than an aware UTC cutoff."""
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        cutoff_iso = cutoff.astimezone(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM market_quotes WHERE julianday(received_at) < julianday(?)",
                (cutoff_iso,),
            )
        return max(cursor.rowcount, 0)

    def vacuum(self) -> None:
        """Reclaim SQLite file space after retention cleanup."""
        with closing(self._connect()) as connection:
            connection.execute("VACUUM")


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

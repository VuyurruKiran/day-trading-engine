from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta


def meta(*, latency_ms: int = 100, origin: str = "http_date") -> ResponseMeta:
    received = datetime.now(UTC)
    return ResponseMeta(
        source_at=received - timedelta(milliseconds=latency_ms),
        received_at=received,
        source_time_origin=origin,
        latency_ms=latency_ms,
        rate_limit_remaining=14990,
        rate_limit_reset=60,
    )


def quote(*, delay: int = 0, halted: bool = False) -> Quote:
    return Quote(
        symbol="AAPL",
        symbolId=8049,
        bidPrice=220.10,
        bidSize=100,
        askPrice=220.20,
        askSize=120,
        lastTradePrice=220.15,
        volume=1_000_000,
        openPrice=218.0,
        highPrice=221.0,
        lowPrice=217.5,
        delay=delay,
        isHalted=halted,
    )


def test_fresh_quote_is_stored_with_provenance(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")

    stored = store.store_quote(quote(), meta())
    latest = store.latest("aapl")

    assert stored.is_trade_eligible is True
    assert stored.invalid_reason is None
    assert latest == stored
    assert latest.source_time_origin == "http_date"
    assert latest.rate_limit_remaining == 14990


def test_delayed_quote_is_never_trade_eligible(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")

    stored = store.store_quote(quote(delay=15), meta())

    assert stored.is_trade_eligible is False
    assert stored.invalid_reason == "DELAYED_QUOTE"


def test_halted_quote_is_never_trade_eligible(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")

    stored = store.store_quote(quote(halted=True), meta())

    assert stored.is_trade_eligible is False
    assert stored.invalid_reason == "HALTED"


def test_unverified_or_stale_source_time_is_rejected(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")

    unverified = store.store_quote(quote(), meta(origin="received_proxy"))
    stale_meta = meta(latency_ms=5_001)
    stale_meta = ResponseMeta(
        source_at=stale_meta.source_at,
        received_at=stale_meta.received_at + timedelta(microseconds=1),
        source_time_origin=stale_meta.source_time_origin,
        latency_ms=stale_meta.latency_ms,
        rate_limit_remaining=stale_meta.rate_limit_remaining,
        rate_limit_reset=stale_meta.rate_limit_reset,
    )
    stale = store.store_quote(quote(), stale_meta)

    assert unverified.invalid_reason == "SOURCE_TIME_UNVERIFIED"
    assert stale.invalid_reason == "STALE_QUOTE"

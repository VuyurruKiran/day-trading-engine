from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from day_trading_engine.market_data.store import MarketDataStore
from day_trading_engine.providers.questrade import Quote, ResponseMeta


def _store_at(store: MarketDataStore, at: datetime, symbol_id: int) -> None:
    """Store one valid quote at a controlled timestamp."""
    store.store_quote(
        Quote(
            symbol=f"T{symbol_id}",
            symbolId=symbol_id,
            bidPrice=9.99,
            bidSize=100,
            askPrice=10.01,
            askSize=100,
            lastTradePrice=10.0,
            volume=1_000,
            openPrice=10.0,
            highPrice=10.1,
            lowPrice=9.9,
            delay=0,
            isHalted=False,
        ),
        ResponseMeta(
            source_at=at,
            received_at=at,
            source_time_origin="http_date",
            latency_ms=1,
            rate_limit_remaining=100,
            rate_limit_reset=None,
        ),
    )


def test_delete_before_removes_only_expired_quotes(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")
    cutoff = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    _store_at(store, cutoff - timedelta(days=1), 1)
    _store_at(store, cutoff, 2)
    _store_at(store, cutoff + timedelta(minutes=1), 3)

    deleted = store.delete_before(cutoff)

    assert deleted == 1
    assert store.latest("T1") is None
    assert store.latest("T2") is not None
    assert store.latest("T3") is not None


def test_delete_before_compares_offset_timestamps_as_instants(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")
    cutoff = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    _store_at(store, datetime(2026, 8, 27, 8, 30, tzinfo=timezone(timedelta(hours=-4))), 1)
    _store_at(store, datetime(2026, 8, 27, 13, 0, tzinfo=timezone(timedelta(hours=2))), 2)

    deleted = store.delete_before(cutoff)

    assert deleted == 1
    assert store.latest("T1") is not None
    assert store.latest("T2") is None


def test_delete_before_requires_aware_cutoff(tmp_path: Path) -> None:
    store = MarketDataStore(tmp_path / "trading.db")

    with pytest.raises(ValueError, match="timezone-aware"):
        store.delete_before(datetime(2026, 8, 27, 12, 0))
